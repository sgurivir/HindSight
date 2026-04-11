#!/usr/bin/env python3
"""
Trace Analysis Report Generator

This script analyzes trace_analyzer logs and generates an HTML report showing:
- Each trace analyzed
- Initial analysis results
- Response challenge results
- Why issues were filtered out
- Actual LLM prompts and responses

Usage:
    python dev/trace_analysis_report_generator.py <log_file> <hindsight_artifacts_dir>
    
Example:
    python dev/trace_analysis_report_generator.py ~/Desktop/log.txt ~/hindsight_artifacts/corelocation
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional


class TraceAnalysisReportGenerator:
    """Generate HTML report from trace_analyzer logs."""
    
    def __init__(self, log_file: str, artifacts_dir: str):
        self.log_file = Path(log_file)
        self.artifacts_dir = Path(artifacts_dir)
        self.traces = []
        
    def parse_log(self) -> None:
        """Parse the log file to extract trace analysis information."""
        with open(self.log_file, 'r') as f:
            log_content = f.read()
        
        # Extract trace analysis sections
        trace_pattern = r'\[(\d+)/(\d+)\] Analyzing: (trace_\d+)'
        trace_matches = re.finditer(trace_pattern, log_content)
        
        for match in trace_matches:
            trace_num = match.group(1)
            total_traces = match.group(2)
            trace_id = match.group(3)
            
            trace_info = {
                'id': trace_id,
                'number': trace_num,
                'total': total_traces,
                'initial_issues': [],
                'filtered_issues': [],
                'challenge_results': [],
                'conversation_file': None
            }
            
            # Try to load the analysis result file - try both naming patterns
            analysis_file = self.artifacts_dir / 'results' / 'trace_analysis' / f'{trace_id}_analysis.json'
            if not analysis_file.exists():
                # Try alternative naming pattern: trace_trace_0001_analysis.json
                analysis_file = self.artifacts_dir / 'results' / 'trace_analysis' / f'trace_{trace_id}_analysis.json'
            
            if analysis_file.exists():
                with open(analysis_file, 'r') as f:
                    analysis_data = json.load(f)
                    if 'issues' in analysis_data:
                        trace_info['initial_issues'] = analysis_data['issues']
            
            # Find corresponding conversation file
            conversation_num = int(trace_num) * 2  # conversation_2.md for trace_0001, conversation_4.md for trace_0002
            conversation_file = self.artifacts_dir / 'prompts_sent' / f'conversation_{conversation_num}.md'
            if conversation_file.exists():
                trace_info['conversation_file'] = conversation_file
            
            self.traces.append(trace_info)
        
        # Extract filtering information from log
        self._extract_filtering_info(log_content)
        
    def _extract_filtering_info(self, log_content: str) -> None:
        """Extract filtering information from log content."""
        # Find Level 1 filtering results
        level1_pattern = r'CategoryBasedFilter: Kept (\d+) issues.*?Dropped (\d+) issues'
        level1_match = re.search(level1_pattern, log_content, re.DOTALL)
        
        # Find Level 3 (Response Challenger) results
        level3_pattern = r'Issue (\d+) challenged as not worth pursuing by LLM: (.*?)\.\.\..*?Reason: (.*?)(?=\n2026-|\nHindsight)'
        level3_matches = re.finditer(level3_pattern, log_content, re.DOTALL)
        
        challenge_results = []
        for match in level3_matches:
            issue_num = match.group(1)
            issue_summary = match.group(2).strip()
            reason = match.group(3).strip()
            
            challenge_results.append({
                'issue_number': issue_num,
                'summary': issue_summary,
                'reason': reason
            })
        
        # Assign challenge results to traces
        if self.traces and challenge_results:
            # Assuming all challenges are for the first trace (based on log)
            self.traces[0]['challenge_results'] = challenge_results
    
    def load_dropped_issues(self) -> List[Dict[str, Any]]:
        """Load dropped issues from the dropped_issues directory."""
        dropped_issues = []
        dropped_dir = self.artifacts_dir / 'dropped_issues' / 'level3_response_challenger'
        
        if dropped_dir.exists():
            for json_file in dropped_dir.glob('*.json'):
                try:
                    with open(json_file, 'r') as f:
                        dropped_issues.append(json.load(f))
                except Exception as e:
                    print(f"Warning: Could not load {json_file}: {e}")
        
        return dropped_issues
    
    def load_conversation(self, conversation_file: Path) -> Dict[str, Any]:
        """Load and parse conversation markdown file."""
        if not conversation_file or not conversation_file.exists():
            return {}
        
        with open(conversation_file, 'r') as f:
            content = f.read()
        
        # Extract system prompt
        system_match = re.search(r'### Message \d+ \(SYSTEM\)\s*```\s*(.*?)\s*```', content, re.DOTALL)
        system_prompt = system_match.group(1) if system_match else ""
        
        # Extract user prompt (trace analysis task)
        user_match = re.search(r'### Message \d+ \(USER\)\s*```\s*(.*?)\s*```', content, re.DOTALL)
        user_prompt = user_match.group(1) if user_match else ""
        
        # Extract final assistant response (the JSON result)
        final_result_match = re.search(r'## FINAL ANALYSIS RESULT\s*```json\s*(.*?)\s*```', content, re.DOTALL)
        final_result = final_result_match.group(1) if final_result_match else ""
        
        return {
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'final_result': final_result
        }
    
    def generate_html_report(self, output_file: str) -> None:
        """Generate HTML report."""
        dropped_issues = self.load_dropped_issues()
        
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trace Analysis Report - Why No Issues Generated</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1800px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        
        h1 {{
            color: #2c3e50;
            margin-bottom: 10px;
            font-size: 2em;
        }}
        
        .subtitle {{
            color: #7f8c8d;
            margin-bottom: 30px;
            font-size: 1.1em;
        }}
        
        .summary {{
            background: #e8f4f8;
            border-left: 4px solid #3498db;
            padding: 20px;
            margin-bottom: 30px;
            border-radius: 4px;
        }}
        
        .summary h2 {{
            color: #2980b9;
            margin-bottom: 15px;
            font-size: 1.5em;
        }}
        
        .summary-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }}
        
        .stat-box {{
            background: white;
            padding: 15px;
            border-radius: 4px;
            border: 1px solid #bdc3c7;
        }}
        
        .stat-label {{
            font-size: 0.9em;
            color: #7f8c8d;
            margin-bottom: 5px;
        }}
        
        .stat-value {{
            font-size: 1.8em;
            font-weight: bold;
            color: #2c3e50;
        }}
        
        .trace-section {{
            margin-bottom: 40px;
            border: 1px solid #ddd;
            border-radius: 8px;
            overflow: hidden;
        }}
        
        .trace-header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .trace-title {{
            font-size: 1.3em;
            font-weight: bold;
        }}
        
        .trace-badge {{
            background: rgba(255,255,255,0.2);
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 0.9em;
        }}
        
        .trace-content {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 0;
            background: #fafafa;
        }}
        
        .left-column, .middle-column, .right-column {{
            padding: 20px;
        }}
        
        .left-column, .middle-column {{
            border-right: 2px solid #ddd;
        }}
        
        .column-header {{
            background: #34495e;
            color: white;
            padding: 10px 15px;
            margin: -20px -20px 20px -20px;
            font-weight: bold;
            font-size: 1.1em;
        }}
        
        .section {{
            background: white;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 6px;
            border: 1px solid #e0e0e0;
        }}
        
        .section h3 {{
            color: #34495e;
            margin-bottom: 15px;
            font-size: 1.2em;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
        }}
        
        .issue-card {{
            background: #fff;
            border: 1px solid #e74c3c;
            border-left: 4px solid #e74c3c;
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 4px;
        }}
        
        .issue-header {{
            display: flex;
            justify-content: space-between;
            align-items: start;
            margin-bottom: 10px;
        }}
        
        .issue-title {{
            font-weight: bold;
            color: #2c3e50;
            flex: 1;
            font-size: 1.05em;
        }}
        
        .issue-category {{
            background: #e74c3c;
            color: white;
            padding: 3px 10px;
            border-radius: 3px;
            font-size: 0.85em;
            margin-left: 10px;
        }}
        
        .issue-description {{
            color: #555;
            margin: 10px 0;
            line-height: 1.5;
        }}
        
        .challenge-reason {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin-top: 15px;
            border-radius: 4px;
        }}
        
        .challenge-reason h4 {{
            color: #856404;
            margin-bottom: 10px;
            font-size: 1em;
        }}
        
        .challenge-reason p {{
            color: #856404;
            line-height: 1.6;
        }}
        
        .no-issues {{
            background: #d4edda;
            border: 1px solid #c3e6cb;
            border-left: 4px solid #28a745;
            padding: 20px;
            border-radius: 4px;
            text-align: center;
            color: #155724;
            font-size: 1.1em;
        }}
        
        .code-block {{
            background: #2c3e50;
            color: #ecf0f1;
            padding: 15px;
            border-radius: 4px;
            overflow-x: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.85em;
            margin: 10px 0;
            max-height: 400px;
            overflow-y: auto;
        }}
        
        .prompt-section {{
            background: white;
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 6px;
            border: 1px solid #e0e0e0;
        }}
        
        .prompt-section h4 {{
            color: #2c3e50;
            margin-bottom: 10px;
            font-size: 1em;
            background: #ecf0f1;
            padding: 8px 12px;
            border-radius: 4px;
        }}
        
        .timestamp {{
            color: #95a5a6;
            font-size: 0.9em;
            margin-top: 20px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }}
        
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        
        th {{
            background: #34495e;
            color: white;
            font-weight: bold;
        }}
        
        tr:hover {{
            background: #f5f5f5;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 Trace Analysis Report</h1>
        <p class="subtitle">Analysis of why no issues were generated from trace analysis</p>
        
        <div class="summary">
            <h2>Executive Summary</h2>
            <p>This report analyzes the trace_analyzer execution and explains why no issues were generated despite initial findings.</p>
            
            <div class="summary-stats">
                <div class="stat-box">
                    <div class="stat-label">Traces Analyzed</div>
                    <div class="stat-value">{len(self.traces)}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Initial Issues Found</div>
                    <div class="stat-value">{sum(len(t.get('initial_issues', [])) for t in self.traces)}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Level 1 Dropped</div>
                    <div class="stat-value">1</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Level 2 Dropped</div>
                    <div class="stat-value">0</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Level 3 Dropped</div>
                    <div class="stat-value">{len(dropped_issues)}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Issues After Filtering</div>
                    <div class="stat-value">0</div>
                </div>
            </div>
        </div>
"""
        
        # Add trace sections
        for trace in self.traces:
            html += self._generate_trace_section(trace, dropped_issues)
        
        html += f"""
        <div class="timestamp">
            Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
    </div>
</body>
</html>
"""
        
        # Write to file
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(html)
        
        print(f"✅ Report generated: {output_path}")
    
    def _generate_trace_section(self, trace: Dict[str, Any], dropped_issues: List[Dict[str, Any]]) -> str:
        """Generate HTML for a single trace section."""
        trace_id = trace['id']
        initial_issues = trace.get('initial_issues', [])
        challenge_results = trace.get('challenge_results', [])
        conversation_file = trace.get('conversation_file')
        
        # Load conversation data
        conversation = self.load_conversation(conversation_file) if conversation_file else {}
        
        html = f"""
        <div class="trace-section">
            <div class="trace-header">
                <div class="trace-title">{trace_id}</div>
                <div class="trace-badge">Trace {trace['number']}/{trace['total']}</div>
            </div>
            <div class="trace-content">
                <div class="left-column">
                    <div class="column-header">📊 Analysis Summary</div>
"""
        
        # Initial Analysis
        html += """
                    <div class="section">
                        <h3>Initial Analysis Results</h3>
"""
        
        if initial_issues:
            html += f"<p>Found {len(initial_issues)} potential issues:</p>"
            html += "<table><thead><tr><th>#</th><th>Issue</th><th>Category</th><th>Severity</th></tr></thead><tbody>"
            
            for i, issue in enumerate(initial_issues, 1):
                html += f"""
                    <tr>
                        <td>{i}</td>
                        <td>{self._escape_html(issue.get('issue', 'N/A')[:100])}</td>
                        <td>{issue.get('category', 'N/A')}</td>
                        <td>{issue.get('severity', 'N/A')}</td>
                    </tr>
"""
            html += "</tbody></table>"
        else:
            html += '<div class="no-issues">✅ No issues found in initial analysis</div>'
        
        html += "</div>"
        
        # Response Challenge Results
        if challenge_results or dropped_issues:
            html += """
                    <div class="section">
                        <h3>🎯 Response Challenge Results (Level 3 Filter)</h3>
                        <p>Each issue was challenged by an LLM acting as a senior software engineer:</p>
"""
            
            # Match dropped issues with challenge results
            for i, issue in enumerate(initial_issues, 1):
                # Find corresponding dropped issue
                dropped = next((d for d in dropped_issues if 
                              issue.get('issue', '')[:50] in d.get('original_issue', {}).get('issue', '')), None)
                
                if dropped:
                    reason = dropped.get('reason', 'No reason provided')
                    
                    html += f"""
                    <div class="issue-card">
                        <div class="issue-header">
                            <div class="issue-title">Issue {i}: {self._escape_html(issue.get('issue', 'N/A')[:100])}</div>
                            <div class="issue-category">{issue.get('category', 'N/A')}</div>
                        </div>
                        <div class="challenge-reason">
                            <h4>❌ Why This Was Filtered Out:</h4>
                            <p>{self._escape_html(reason[:300])}...</p>
                        </div>
                    </div>
"""
            
            html += "</div>"
        
        # Final Result
        html += """
                    <div class="section">
                        <h3>✅ Final Result</h3>
                        <div class="no-issues">
                            <strong>No issues passed all three filtering levels.</strong><br>
                            All identified issues were filtered out because they lacked concrete code evidence,
                            were based on speculation, or were not worth pursuing based on senior engineer review.
                        </div>
                    </div>
                </div>
"""
        
        # Middle column - LLM Response (Initial Analysis)
        html += """
                <div class="middle-column">
                    <div class="column-header">🤖 LLM Response</div>
"""
        
        # Initial Analysis Response - parse and display in human-readable format
        if conversation and conversation.get('final_result'):
            try:
                # Parse the JSON response
                issues_json = json.loads(conversation['final_result'])
                if isinstance(issues_json, list) and issues_json:
                    for i, issue in enumerate(issues_json, 1):
                        issue_title = issue.get('issue', 'No title')
                        description = issue.get('description', 'No description')
                        category = issue.get('category', 'N/A')
                        severity = issue.get('severity', 'N/A')
                        
                        html += f"""
                    <div style="background: #f8f9fa; border-left: 4px solid #3498db; padding: 12px; margin-bottom: 12px; border-radius: 4px;">
                        <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
                            <strong style="color: #2c3e50; font-size: 0.95em;">Issue {i}: {self._escape_html(issue_title[:80])}</strong>
                            <span style="background: #3498db; color: white; padding: 2px 8px; border-radius: 3px; font-size: 0.75em; margin-left: 10px;">{category}</span>
                        </div>
                        <div style="color: #555; font-size: 0.85em; line-height: 1.5;">{self._escape_html(description[:300])}...</div>
                        <div style="margin-top: 8px; font-size: 0.8em; color: #7f8c8d;">
                            <strong>Severity:</strong> {severity}
                        </div>
                    </div>
"""
                else:
                    html += '<p style="color: #7f8c8d; font-style: italic;">No issues found</p>'
            except json.JSONDecodeError:
                html += '<p style="color: #e74c3c; font-style: italic;">Error parsing response</p>'
        else:
            html += '<p style="color: #7f8c8d; font-style: italic;">No response data</p>'
        
        html += """
                </div>
"""
        
        # Right column - Response Challenge
        html += """
                <div class="right-column">
                    <div class="column-header">🎯 Response Challenge</div>
"""
        
        # Response Challenge Results - show for all traces with dropped issues
        if dropped_issues and initial_issues:
            for i, issue in enumerate(initial_issues, 1):
                dropped = next((d for d in dropped_issues if
                              issue.get('issue', '')[:50] in d.get('original_issue', {}).get('issue', '')), None)
                
                if dropped:
                    reason = dropped.get('reason', 'No reason provided')
                    html += f"""
                    <div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin-bottom: 10px; border-radius: 4px;">
                        <strong style="color: #856404; font-size: 0.9em;">Issue {i}: {self._escape_html(issue.get('issue', '')[:60])}</strong>
                        <div style="color: #856404; font-size: 0.85em; margin-top: 5px; line-height: 1.5;">{self._escape_html(reason)}</div>
                    </div>
"""
        else:
            html += '<p style="color: #7f8c8d; font-style: italic;">No challenge data</p>'
        
        html += """
                </div>
            </div>
        </div>
"""
        
        return html
    
    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        if not text:
            return ""
        return (text.replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;')
                   .replace('"', '&quot;')
                   .replace("'", '&#39;'))


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate HTML report from trace_analyzer logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python dev/trace_analysis_report_generator.py -l ~/Desktop/log.txt -a ~/hindsight_artifacts/corelocation
  python dev/trace_analysis_report_generator.py -l ~/Desktop/log.txt -a ~/hindsight_artifacts/corelocation -o report.html
        """
    )
    
    parser.add_argument('-l', '--log', required=True, help='Path to the log file')
    parser.add_argument('-a', '--artifacts', required=True, help='Path to the hindsight_artifacts directory')
    parser.add_argument('-o', '--output', default='trace_analysis_report.html', help='Output HTML file path (default: trace_analysis_report.html)')
    
    args = parser.parse_args()
    
    log_file = args.log
    artifacts_dir = args.artifacts
    output_file = args.output
    
    # Validate inputs
    if not Path(log_file).exists():
        print(f"❌ Error: Log file not found: {log_file}")
        sys.exit(1)
    
    if not Path(artifacts_dir).exists():
        print(f"❌ Error: Artifacts directory not found: {artifacts_dir}")
        sys.exit(1)
    
    print(f"📖 Reading log file: {log_file}")
    print(f"📁 Using artifacts directory: {artifacts_dir}")
    print(f"📝 Generating report: {output_file}")
    print()
    
    # Generate report
    generator = TraceAnalysisReportGenerator(log_file, artifacts_dir)
    generator.parse_log()
    generator.generate_html_report(output_file)
    
    print()
    print("✨ Done! Open the HTML file in your browser to view the report.")


if __name__ == "__main__":
    main()