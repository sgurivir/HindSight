#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""
Code Analysis Differ
Compares analysis results from two artifact directories and generates a diff HTML report
showing issues present in current but not in baseline.
"""

import argparse
import glob
import json
import logging
import os
import sys
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from ..utils.file_util import read_json_file
from ..utils.log_util import get_logger

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from results_store.code_analysis_publisher import CodeAnalysisResultsPublisher
from results_store.code_analysys_results_local_fs_subscriber import CodeAnalysysResultsLocalFSSubscriber

# Constants
ANALYSIS_FILE_SUFFIX = "_analysis.json"
CODE_ANALYSIS_SUBDIR = "code_analysis"
DIFF_HTML_TEMPLATE = "diff_analysis_{date}.html"

class CodeAnalysisDiffer:
    """Main class for comparing code analysis results between two artifact directories."""

    def __init__(self, baseline_dir: str, current_dir: str, output_dir: str):
        """
        Initialize the differ.

        Args:
            baseline_dir: Path to baseline artifacts directory
            current_dir: Path to current artifacts directory
            output_dir: Path to output directory for diff report
        """
        self.baseline_dir = baseline_dir
        self.current_dir = current_dir
        self.output_dir = output_dir
        self.logger = get_logger(__name__)

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

    def load_issues_from_publisher(self, artifacts_dir: str) -> List[Dict[str, Any]]:
        """
        Load all issues from artifacts directory using publisher-subscriber mechanism.

        Args:
            artifacts_dir: Path to artifacts directory

        Returns:
            List of issue dictionaries
        """
        all_issues = []

        # Look for repo directories in artifacts directory
        for repo_dir in glob.glob(os.path.join(artifacts_dir, "*")):
            if os.path.isdir(repo_dir):
                repo_name = os.path.basename(repo_dir)

                # Initialize publisher-subscriber system
                publisher = CodeAnalysisResultsPublisher()
                subscriber = CodeAnalysysResultsLocalFSSubscriber(artifacts_dir)
                subscriber.set_repo_name(repo_name)

                # Load existing results from files into publisher
                loaded_count = subscriber.load_existing_results(repo_name, publisher)

                if loaded_count > 0:
                    # Get all results from publisher
                    all_results = publisher.get_results(repo_name)

                    # Convert results to issues format
                    for result in all_results:
                        if 'results' in result and isinstance(result['results'], list):
                            all_issues.extend(result['results'])
                        else:
                            all_issues.append(result)

                    self.logger.info(f"Loaded {len(all_results)} results from repo {repo_name} via publisher-subscriber")

        self.logger.info(f"Loaded {len(all_issues)} total issues from artifacts directory")
        return all_issues

    def create_issue_signature(self, issue: Dict[str, Any]) -> str:
        """
        Create a unique signature for an issue to enable comparison.

        Args:
            issue: Issue dictionary

        Returns:
            Unique signature string
        """
        # Use key fields to create a signature
        file_name = issue.get('file_name', 'unknown')
        function = issue.get('function', 'unknown')
        lines = str(issue.get('lines', 'unknown'))
        description = issue.get('description', issue.get('issue', ''))

        # Create a normalized signature
        signature = f"{file_name}::{function}::{lines}::{description[:100]}"
        return signature.lower().strip()

    def find_new_issues(self, baseline_issues: List[Dict[str, Any]],
                       current_issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Find issues that are in current but not in baseline.

        Args:
            baseline_issues: List of baseline issues
            current_issues: List of current issues

        Returns:
            List of new issues (present in current but not baseline)
        """
        # Create signatures for baseline issues
        baseline_signatures = set()
        for issue in baseline_issues:
            signature = self.create_issue_signature(issue)
            baseline_signatures.add(signature)

        self.logger.info(f"Created {len(baseline_signatures)} baseline signatures")

        # Find current issues not in baseline
        new_issues = []
        for issue in current_issues:
            signature = self.create_issue_signature(issue)
            if signature not in baseline_signatures:
                new_issues.append(issue)

        self.logger.info(f"Found {len(new_issues)} new issues not present in baseline")
        return new_issues

    def calculate_diff_stats(self, new_issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate statistics for the diff report.

        Args:
            new_issues: List of new issues

        Returns:
            Statistics dictionary
        """
        stats = {
            'total': len(new_issues),
            'critical': 0,
            'high': 0,
            'medium': 0,
            'low': 0,
            'by_file': defaultdict(int),
            'by_function': defaultdict(int)
        }

        for issue in new_issues:
            # Count by severity/kind
            severity = issue.get('kind', issue.get('severity', 'unknown')).lower()
            if severity in stats:
                stats[severity] += 1

            # Count by file
            file_name = issue.get('file_name', issue.get('file', 'unknown'))
            stats['by_file'][file_name] += 1

            # Count by function
            function = issue.get('function', 'unknown')
            stats['by_function'][function] += 1

        return stats

    def generate_diff_html_report(self, new_issues: List[Dict[str, Any]],
                                 baseline_dir: str, current_dir: str) -> str:
        """
        Generate HTML report for the diff results.

        Args:
            new_issues: List of new issues to include in report
            baseline_dir: Path to baseline directory
            current_dir: Path to current directory

        Returns:
            Path to generated HTML file
        """
        stats = self.calculate_diff_stats(new_issues)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = os.path.join(self.output_dir, DIFF_HTML_TEMPLATE.format(date=timestamp))

        html_content = self._generate_html_template(new_issues, stats, baseline_dir, current_dir)

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)

        self.logger.info(f"Generated diff HTML report: {output_file}")
        return output_file

    def _generate_html_template(self, issues: List[Dict[str, Any]], stats: Dict[str, Any],
                               _baseline_dir: str = None, _current_dir: str = None) -> str:
        """Generate the HTML template for the diff report."""

        # Convert issues to JSON for JavaScript
        issues_json = json.dumps({"issues": issues}, indent=8)

        html_template = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hindsight Code Analysis Diff Report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display",
                         "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif;
            background: #f5f5f7;
            min-height: 100vh;
            color: #1d1d1f;
            padding: 20px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 15px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 600;
        }}

        .header .subtitle {{
            font-size: 1.2em;
            opacity: 0.9;
            margin-bottom: 20px;
        }}

        .comparison-info {{
            background: rgba(255,255,255,0.1);
            border-radius: 10px;
            padding: 20px;
            margin-top: 20px;
        }}

        .comparison-info h3 {{
            margin-bottom: 15px;
            font-size: 1.1em;
        }}

        .comparison-paths {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }}

        .path-info {{
            background: rgba(255,255,255,0.1);
            padding: 15px;
            border-radius: 8px;
        }}

        .path-label {{
            font-weight: 600;
            margin-bottom: 5px;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .path-value {{
            font-family: monospace;
            font-size: 0.9em;
            word-break: break-all;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f5f5f7;
        }}

        .stat-card {{
            background: #ffffff;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
            box-shadow: 0 1px 2px rgba(0,0,0,0.06);
            transition: transform 0.2s ease;
            border: 1px solid rgba(0,0,0,0.06);
        }}

        .stat-card:hover {{
            transform: translateY(-3px);
        }}

        .stat-number {{
            font-size: 2.5em;
            font-weight: 700;
            margin-bottom: 5px;
            letter-spacing: -0.02em;
        }}

        .stat-label {{
            color: #6e6e73;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        .critical {{ color: #b00020; }}
        .high {{ color: #bf2600; }}
        .medium {{ color: #b35c00; }}
        .low {{ color: #2a7f3f; }}
        .total {{ color: #06c; }}
        .new {{ color: #ff6b35; }}

        .filters {{
            padding: 20px 30px;
            background: #f5f5f7;
            border-bottom: 1px solid rgba(0,0,0,0.08);
        }}

        .filter-group {{
            display: flex;
            gap: 15px;
            align-items: center;
            flex-wrap: wrap;
            justify-content: center;
        }}

        .filter-btn {{
            padding: 8px 16px;
            border: 1px solid rgba(0,0,0,0.15);
            background: #ffffff;
            color: #1d1d1f;
            border-radius: 20px;
            cursor: pointer;
            transition: background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease;
            font-size: 0.9em;
        }}

        .filter-btn:hover,
        .filter-btn.active {{
            background: #1d1d1f;
            color: #fff;
            border-color: #1d1d1f;
        }}

        .issues-container {{
            padding: 30px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 2rem;
            align-items: start;
        }}

        .issue {{
            background: #fff;
            border: 2px solid #ff6b35;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(255,107,53,0.1);
            height: fit-content;
        }}

        .issue__head {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1rem 1.25rem;
            border-bottom: 1px solid #ff6b35;
            background: rgba(255,107,53,0.05);
        }}

        .issue__file {{
            font-size: 1.05rem;
            margin: 0;
            font-weight: 600;
        }}

        .issue__meta {{
            padding: .75rem 1.25rem;
            background: #f8f9fa;
            border-bottom: 1px solid #e5e5ea;
        }}

        .kv {{
            display: flex;
            gap: 1.5rem;
            flex-wrap: wrap;
            margin: 0;
        }}

        .kv dt {{
            font-weight: 600;
            color: #424245;
        }}

        .kv dd {{
            margin: 0 .25rem 0 0;
        }}

        .issue__body {{
            padding: 1rem 1.25rem;
        }}

        .issue__title {{
            font-size: 1rem;
            margin: .25rem 0 .5rem;
            font-weight: 600;
        }}

        .badge {{
            display: inline-block;
            padding: .2rem .6rem;
            border-radius: 999px;
            font-size: .75rem;
            font-weight: 600;
        }}

        .badge--severity {{
            background: #fee;
            color: #b00020;
            border: 1px solid #f6d;
        }}

        .badge--new {{
            background: #fff3e0;
            color: #ff6b35;
            border: 1px solid #ffcc80;
        }}

        .callout {{
            border: 1px solid #e5e5ea;
            border-radius: 10px;
            padding: .75rem .9rem;
            margin: .75rem 0;
            background: #fff;
        }}

        .callout summary {{
            font-weight: 600;
            cursor: pointer;
        }}

        .callout--impact {{
            background: #fff8e1;
            border-color: #ffe4a3;
        }}

        .callout--solution {{
            background: #f0f7f0;
            border-color: #cfe8cf;
        }}

        code {{
            background: #f2f2f7;
            padding: 0 .25rem;
            border-radius: 4px;
        }}

        .hidden {{
            display: none;
        }}

        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #6e6e73;
        }}

        .empty-state h3 {{
            font-size: 1.5em;
            margin-bottom: 10px;
            color: #2a7f3f;
        }}

        @media (max-width: 768px) {{
            .comparison-paths {{
                grid-template-columns: 1fr;
            }}

            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
                padding: 15px;
                gap: 10px;
            }}

            .issues-container {{
                grid-template-columns: 1fr;
                gap: 1.5rem;
                padding: 15px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Code Analysis Diff Report</h1>
            <div style="margin-top: 20px; text-align: center; font-size: 1.1em;">
                <strong>Generated:</strong> {timestamp}
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-number new">{total}</div>
                <div class="stat-label">New Issues</div>
            </div>
            <div class="stat-card">
                <div class="stat-number critical">{critical}</div>
                <div class="stat-label">Critical</div>
            </div>
            <div class="stat-card">
                <div class="stat-number high">{high}</div>
                <div class="stat-label">High</div>
            </div>
            <div class="stat-card">
                <div class="stat-number medium">{medium}</div>
                <div class="stat-label">Medium</div>
            </div>
            <div class="stat-card">
                <div class="stat-number low">{low}</div>
                <div class="stat-label">Low</div>
            </div>
        </div>

        <div class="filters">
            <div class="filter-group">
                <button class="filter-btn active" data-filter="all">All New Issues</button>
                <button class="filter-btn" data-filter="critical">Critical</button>
                <button class="filter-btn" data-filter="high">High</button>
                <button class="filter-btn" data-filter="medium">Medium</button>
                <button class="filter-btn" data-filter="low">Low</button>
            </div>
        </div>

        <div class="issues-container" id="issuesContainer">
            <!-- Issues will be populated by JavaScript -->
        </div>
    </div>

    <script>
        const issuesData = {issues_json};

        function renderIssues(issues) {{
            const container = document.getElementById('issuesContainer');
            container.innerHTML = '';

            if (issues.length === 0) {{
                container.innerHTML = `
                    <div class="empty-state">
                        <h3>🎉 No New Issues Found!</h3>
                        <p>The current analysis doesn't contain any issues that weren't already present in the baseline.</p>
                    </div>
                `;
                return;
            }}

            issues.forEach((issue, index) => {{
                const issueSection = document.createElement('section');
                issueSection.className = 'issue';
                issueSection.setAttribute('data-severity', issue.kind || issue.severity);

                const fileName = issue.file_name || 'Unknown File';
                const severity = issue.kind || issue.severity || 'unknown';
                const severityUpper = severity.toUpperCase();

                issueSection.innerHTML = `
                    <header class="issue__head">
                        <h2 class="issue__file">${{fileName}}</h2>
                        <div>
                            <span class="badge badge--new">NEW</span>
                            <span class="badge badge--severity">${{severityUpper}}</span>
                        </div>
                    </header>

                    <div class="issue__meta">
                        <dl class="kv">
                            <div><dt>Function</dt><dd><code>${{issue.function || 'Unknown'}}</code></dd></div>
                            <div><dt>Line</dt><dd>${{issue.lines || issue.line || 'N/A'}}</dd></div>
                            <div><dt>Type</dt><dd>${{formatIssueType(issue.issueType || 'unknown')}}</dd></div>
                        </dl>
                    </div>

                    <article class="issue__body">
                        <h3 class="issue__title">Issue Description</h3>
                        <p>${{issue.description || issue.issue || 'No description available'}}</p>

                        ${{(issue.Impact || issue.impact) ? `
                        <details class="callout callout--impact" open>
                            <summary>Impact</summary>
                            <p>${{issue.Impact || issue.impact}}</p>
                        </details>
                        ` : ''}}

                        <details class="callout callout--solution">
                            <summary>Potential Solution</summary>
                            <p>${{issue['Potential solution'] || issue.potentialSolution || 'No solution provided'}}</p>
                        </details>
                    </article>
                `;

                container.appendChild(issueSection);
            }});
        }}

        function formatIssueType(type) {{
            if (type === 'unknown') return 'Unknown';

            let formatted = type.replace(/([a-z])([A-Z])/g, '$1 $2');
            formatted = formatted.replace(/([A-Z])([A-Z][a-z])/g, '$1 $2');
            formatted = formatted.replace(/_/g, ' ');
            formatted = formatted.replace(/\b\w/g, l => l.toUpperCase());

            return formatted;
        }}

        function filterIssues(severity) {{
            let filteredIssues = issuesData.issues;

            if (severity !== 'all') {{
                filteredIssues = filteredIssues.filter(issue => (issue.kind || issue.severity) === severity);
            }}

            renderIssues(filteredIssues);
        }}

        // Initialize
        renderIssues(issuesData.issues);

        // Filter functionality
        document.querySelectorAll('.filter-btn').forEach(btn => {{
            btn.addEventListener('click', (e) => {{
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                filterIssues(e.target.getAttribute('data-filter'));
            }});
        }});
    </script>
</body>
</html>'''.format(
            timestamp=datetime.now().strftime('%B %d, %Y at %I:%M %p'),
            total=stats['total'],
            critical=stats['critical'],
            high=stats['high'],
            medium=stats['medium'],
            low=stats['low'],
            issues_json=issues_json
        )

        return html_template

    def run_diff(self) -> str:
        """
        Run the complete diff analysis.

        Returns:
            Path to generated HTML report
        """
        self.logger.info("Starting code analysis diff...")
        self.logger.info(f"Baseline directory: {self.baseline_dir}")
        self.logger.info(f"Current directory: {self.current_dir}")
        self.logger.info(f"Output directory: {self.output_dir}")

        # Load issues using publisher-subscriber mechanism
        baseline_issues = self.load_issues_from_publisher(self.baseline_dir)
        current_issues = self.load_issues_from_publisher(self.current_dir)

        if not baseline_issues:
            self.logger.warning(f"No analysis results found in baseline directory: {self.baseline_dir}")

        if not current_issues:
            self.logger.warning(f"No analysis results found in current directory: {self.current_dir}")
            # If current is empty, we'll generate an empty report showing no new issues

        # Handle special cases
        if not baseline_issues and current_issues:
            self.logger.info("Baseline is empty - all current issues will be shown as new")
        elif not current_issues:
            self.logger.info("Current directory is empty - no new issues to report")

        self.logger.info(f"Loaded {len(baseline_issues)} baseline issues and {len(current_issues)} current issues")

        # Find new issues
        new_issues = self.find_new_issues(baseline_issues, current_issues)

        # Generate diff report
        report_path = self.generate_diff_html_report(new_issues, self.baseline_dir, self.current_dir)

        self.logger.info(f"Diff analysis completed. Found {len(new_issues)} new issues.")
        self.logger.info(f"Report generated: {report_path}")

        return report_path


def main():
    """Main entry point for the code analysis differ."""
    parser = argparse.ArgumentParser(
        description="Compare code analysis results between two artifact directories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -b /path/to/baseline/artifacts -c /path/to/current/artifacts -o /path/to/output
  %(prog)s --baseline ~/artifacts/baseline --current ~/artifacts/current --output ~/diff_reports
        """
    )

    parser.add_argument(
        "-b", "--baseline",
        required=True,
        help="Path to baseline artifacts directory"
    )

    parser.add_argument(
        "-c", "--current",
        required=True,
        help="Path to current artifacts directory"
    )

    parser.add_argument(
        "-o", "--output",
        required=True,
        help="Path to output directory for diff report"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    try:
        # Validate input directories
        if not os.path.exists(args.baseline):
            print(f"Error: Baseline directory does not exist: {args.baseline}")
            sys.exit(1)

        if not os.path.exists(args.current):
            print(f"Error: Current directory does not exist: {args.current}")
            sys.exit(1)

        # Create differ and run analysis
        differ = CodeAnalysisDiffer(args.baseline, args.current, args.output)
        report_path = differ.run_diff()

        print(f"\n✅ Diff analysis completed successfully!")
        print(f"📊 Report generated: {report_path}")
        print(f"\nOpen the HTML file in your browser to view the results.")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        if args.verbose:
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()