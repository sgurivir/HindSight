"""
HTML report generator for annotated deduplication reports.

This module generates HTML reports that annotate the original static analyzer
report with deduplication information, showing potential duplicate issues
for each issue while preserving the original format.
"""

import json
import logging
import re
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..deduper.issue import Issue, DedupeMatch

logger = logging.getLogger("issue_tracking_deduper.html_generator")


# CSS styles for deduplication annotations - designed to blend with original format
DEDUPE_STYLES = """
<style>
/* Deduplication annotation styles - blends with StaticIntelligence format */

/* Issue card with dedupe match - light yellow background */
.issue-card.has-dedupe,
div.issue-card.has-dedupe,
.issues-container .issue-card.has-dedupe,
.main-content .issue-card.has-dedupe {
    background: #fef9c3 !important;
    background-color: #fef9c3 !important;
    border-left-color: #ca8a04 !important;
    border-left-width: 4px !important;
}

/* Make child elements of deduped cards have transparent/yellow backgrounds */
.issue-card.has-dedupe .issue-header {
    background: rgba(202, 138, 4, 0.15) !important;
}

.issue-card.has-dedupe .issue-content {
    background: transparent !important;
}

/* Dedupe info section within issue card */
.dedupe-info {
    background: rgba(202, 138, 4, 0.1);
    border: 1px solid rgba(202, 138, 4, 0.3);
    border-radius: 8px;
    padding: 12px 16px;
    margin-top: 16px;
}

.dedupe-info-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
    font-weight: 600;
    color: #a16207;
    font-size: 0.9375rem;
}

.dedupe-info-header .dedupe-icon {
    font-size: 1.1em;
}

.dedupe-matches-list {
    list-style: none;
    padding: 0;
    margin: 0;
}

.dedupe-match-item {
    background: rgba(255, 255, 255, 0.8);
    border-radius: 6px;
    padding: 10px 12px;
    margin: 6px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.dedupe-match-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
}

.dedupe-issue-link {
    font-family: 'SF Mono', Monaco, monospace;
    font-weight: 600;
    color: #0066cc;
    text-decoration: underline;
    font-size: 0.875rem;
    cursor: pointer;
}

.dedupe-issue-link:hover {
    text-decoration: underline;
    color: #004499;
}

.dedupe-issue-link:visited {
    color: #551a8b;
}

.dedupe-issue-url {
    font-family: 'SF Mono', Monaco, monospace;
    color: #6b7280;
    font-size: 0.8125rem;
}

.dedupe-score {
    background: #ca8a04;
    color: white;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.75rem;
    font-weight: 600;
    white-space: nowrap;
}

.dedupe-score.high {
    background: #dc2626;
}

.dedupe-score.moderate {
    background: #f59e0b;
    color: #1f2937;
}

.dedupe-score.low {
    background: #10b981;
}

.dedupe-issue-title {
    font-size: 0.875rem;
    color: #374151;
    font-weight: 500;
    margin: 4px 0;
}

/* Summary banner at top */
.dedupe-summary-banner {
    background: linear-gradient(135deg, #ca8a04 0%, #eab308 100%);
    color: white;
    padding: 20px 24px;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 24px;
    align-items: center;
    justify-content: space-between;
}

.dedupe-summary-title {
    font-size: 1.125rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
}

.dedupe-summary-stats {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
}

.dedupe-stat {
    text-align: center;
    background: rgba(255, 255, 255, 0.15);
    padding: 8px 16px;
    border-radius: 8px;
}

.dedupe-stat-value {
    display: block;
    font-size: 1.5rem;
    font-weight: 700;
}

.dedupe-stat-label {
    display: block;
    font-size: 0.75rem;
    opacity: 0.9;
}

.dedupe-summary-note {
    font-size: 0.8125rem;
    opacity: 0.9;
}
</style>
"""


class AnnotatedReportGenerator:
    """
    Generates annotated HTML reports with deduplication information.
    
    This class takes an original HTML report and deduplication results,
    and produces a new HTML report with annotations showing potential
    duplicate issues for each issue while preserving the original format.
    """
    
    def __init__(self):
        """Initialize the report generator."""
        pass
    
    def generate(
        self,
        original_report_path: Path,
        dedupe_results: Dict[str, Dict[str, Any]],
        output_path: Path
    ) -> None:
        """
        Generate an annotated HTML report.
        
        Args:
            original_report_path: Path to the original HTML report.
            dedupe_results: Dictionary mapping issue IDs to their match results.
            output_path: Path for the output HTML file.
        """
        logger.info(f"Generating annotated report: {output_path}")
        
        # Read original HTML
        with open(original_report_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # Calculate summary statistics
        summary = self._calculate_summary(dedupe_results)
        
        # Inject styles
        html_content = self._inject_styles(html_content)
        
        # Inject summary banner
        html_content = self._inject_summary_banner(html_content, summary)
        
        # Modify issuesData to include dedupe info
        html_content = self._inject_dedupe_into_issues_data(
            html_content, dedupe_results, original_report_path.stem
        )
        
        # Modify renderIssues function to display dedupe info
        html_content = self._modify_render_issues_function(html_content)
        
        # Write output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"Annotated report saved to: {output_path}")
    
    def _calculate_summary(
        self,
        dedupe_results: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calculate summary statistics from deduplication results.
        """
        total_issues = len(dedupe_results)
        issues_with_matches = sum(
            1 for r in dedupe_results.values() if r.get("matches")
        )
        total_matches = sum(
            len(r.get("matches", [])) for r in dedupe_results.values()
        )
        
        high_confidence = 0
        moderate_confidence = 0
        low_confidence = 0
        
        for result in dedupe_results.values():
            for match in result.get("matches", []):
                if match.confidence_level == "high":
                    high_confidence += 1
                elif match.confidence_level == "moderate":
                    moderate_confidence += 1
                else:
                    low_confidence += 1
        
        return {
            "total_issues": total_issues,
            "issues_with_matches": issues_with_matches,
            "issues_without_matches": total_issues - issues_with_matches,
            "total_matches": total_matches,
            "high_confidence_matches": high_confidence,
            "moderate_confidence_matches": moderate_confidence,
            "low_confidence_matches": low_confidence,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    
    def _inject_styles(self, html_content: str) -> str:
        """Inject CSS styles into the HTML content."""
        if '</head>' in html_content:
            return html_content.replace('</head>', f'{DEDUPE_STYLES}</head>')
        return DEDUPE_STYLES + html_content
    
    def _inject_summary_banner(
        self,
        html_content: str,
        summary: Dict[str, Any]
    ) -> str:
        """Inject the summary banner into the HTML content."""
        if summary['issues_with_matches'] == 0:
            return html_content
        
        summary_html = f"""
<div class="dedupe-summary-banner">
    <div class="dedupe-summary-title">
        <span>🔍</span>
        <span>Deduplication Analysis</span>
    </div>
    <div class="dedupe-summary-stats">
        <div class="dedupe-stat">
            <span class="dedupe-stat-value">{summary['issues_with_matches']}</span>
            <span class="dedupe-stat-label">Issues with Matches</span>
        </div>
        <div class="dedupe-stat">
            <span class="dedupe-stat-value">{summary['high_confidence_matches']}</span>
            <span class="dedupe-stat-label">High Confidence</span>
        </div>
        <div class="dedupe-stat">
            <span class="dedupe-stat-value">{summary['moderate_confidence_matches']}</span>
            <span class="dedupe-stat-label">Moderate</span>
        </div>
    </div>
    <div class="dedupe-summary-note">
        ⚠️ Issues with potential duplicates are highlighted in yellow. Review before filing new issues.
    </div>
</div>
"""
        
        # Try to inject after the header section
        header_end = re.search(r'</header>', html_content, re.IGNORECASE)
        if header_end:
            insert_pos = header_end.end()
            return html_content[:insert_pos] + summary_html + html_content[insert_pos:]
        
        # Try to inject after .header div
        header_div_end = re.search(r'<div class="header"[^>]*>.*?</div>\s*</div>', html_content, re.IGNORECASE | re.DOTALL)
        if header_div_end:
            insert_pos = header_div_end.end()
            return html_content[:insert_pos] + summary_html + html_content[insert_pos:]
        
        # Try to inject after <body> tag
        body_match = re.search(r'<body[^>]*>', html_content, re.IGNORECASE)
        if body_match:
            insert_pos = body_match.end()
            return html_content[:insert_pos] + summary_html + html_content[insert_pos:]
        
        return html_content
    
    def _generate_issue_id(self, issue_data: Dict[str, Any], index: int, report_name: str) -> str:
        """Generate issue ID matching the Python parser."""
        key_parts = [
            issue_data.get('issue', ''),
            issue_data.get('file', ''),
            issue_data.get('functionName', ''),
            str(issue_data.get('line', ''))
        ]
        key_string = '|'.join(key_parts)
        hash_value = hashlib.md5(key_string.encode()).hexdigest()[:8]
        return f"si_{report_name}_{index}_{hash_value}"
    
    def _inject_dedupe_into_issues_data(
        self,
        html_content: str,
        dedupe_results: Dict[str, Dict[str, Any]],
        report_name: str
    ) -> str:
        """
        Modify the issuesData JSON to include dedupe information for each issue.
        """
        # Find the issuesData JSON
        start_match = re.search(r'const\s+issuesData\s*=\s*', html_content)
        if not start_match:
            logger.warning("Could not find issuesData in HTML")
            return html_content
        
        json_start = start_match.end()
        
        # Extract the JSON object
        json_str = self._extract_json_object(html_content, json_start)
        if not json_str:
            logger.warning("Could not extract issuesData JSON")
            return html_content
        
        # Parse the JSON
        try:
            # Clean the JSON string
            cleaned_json = self._clean_json_string(json_str)
            issues_data = json.loads(cleaned_json)
        except json.JSONDecodeError as e:
            logger.warning(f"Could not parse issuesData JSON: {e}")
            return html_content
        
        # Add dedupe info to each issue
        issues = issues_data.get('issues', [])
        for i, issue in enumerate(issues):
            issue_id = self._generate_issue_id(issue, i, report_name)
            result = dedupe_results.get(issue_id)
            
            if result and result.get('matches'):
                matches = result['matches']
                issue['dedupeMatches'] = [
                    {
                        'issueId': m.issue_id,
                        'issueUrl': m.issue_url,
                        'issueTitle': self._clean_issue_title(m.issue_title, m.issue_url),
                        'similarityPercentage': m.similarity_percentage,
                        'confidenceLevel': m.confidence_level,
                        'matchReason': m.match_reason,
                    }
                    for m in matches
                ]
        
        # Serialize back to JSON
        new_json = json.dumps(issues_data, indent=2, ensure_ascii=False)
        
        # Replace the old JSON with the new one
        json_end = json_start + len(json_str)
        html_content = html_content[:json_start] + new_json + html_content[json_end:]
        
        return html_content
    
    def _extract_json_object(self, content: str, start_pos: int) -> Optional[str]:
        """Extract a JSON object from content starting at the given position."""
        if start_pos >= len(content) or content[start_pos] != '{':
            return None
        
        brace_count = 0
        in_string = False
        escape_next = False
        
        for i in range(start_pos, len(content)):
            char = content[i]
            
            if escape_next:
                escape_next = False
                continue
            
            if char == '\\' and in_string:
                escape_next = True
                continue
            
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            
            if in_string:
                continue
            
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return content[start_pos:i + 1]
        
        return None
    
    def _clean_json_string(self, json_str: str) -> str:
        """Clean up a JSON string extracted from JavaScript."""
        result = []
        in_string = False
        escape_next = False
        i = 0
        
        while i < len(json_str):
            char = json_str[i]
            
            if escape_next:
                result.append(char)
                escape_next = False
                i += 1
                continue
            
            if char == '\\' and in_string:
                result.append(char)
                escape_next = True
                i += 1
                continue
            
            if char == '"':
                in_string = not in_string
                result.append(char)
            elif in_string:
                if char == '\n':
                    result.append('\\n')
                elif char == '\r':
                    result.append('\\r')
                elif char == '\t':
                    result.append('\\t')
                elif ord(char) < 32:
                    result.append(f'\\u{ord(char):04x}')
                else:
                    result.append(char)
            else:
                if char == ',':
                    j = i + 1
                    while j < len(json_str) and json_str[j] in ' \t\n\r':
                        j += 1
                    if j < len(json_str) and json_str[j] in ']}':
                        i += 1
                        continue
                result.append(char)
            
            i += 1
        
        return ''.join(result)
    
    def _modify_render_issues_function(self, html_content: str) -> str:
        """
        Modify the renderIssues function to display dedupe information.
        """
        # Find the renderIssues function and modify it to include dedupe info
        # Look for the issue card innerHTML template
        
        # Pattern to find the issue card innerHTML assignment
        pattern = r"(issueCard\.innerHTML\s*=\s*`[\s\S]*?)(</div>\s*`\s*;)"
        
        # Find the last </div> before the closing backtick
        match = re.search(pattern, html_content)
        if not match:
            logger.warning("Could not find issueCard.innerHTML in HTML")
            return html_content
        
        # Build the dedupe HTML template to insert
        dedupe_template = """
                ${issue.dedupeMatches && issue.dedupeMatches.length > 0 ? `
                <div class="dedupe-info">
                    <div class="dedupe-info-header">
                        <span class="dedupe-icon">${issue.dedupeMatches.some(m => m.confidenceLevel === 'high') ? '🔴' : '🟡'}</span>
                        <span>${issue.dedupeMatches.some(m => m.confidenceLevel === 'high') ? 'Likely Duplicate' : 'Potential Duplicate'} Found (${issue.dedupeMatches.length} match${issue.dedupeMatches.length > 1 ? 'es' : ''})</span>
                    </div>
                    <ul class="dedupe-matches-list">
                        ${issue.dedupeMatches.map(match => `
                        <li class="dedupe-match-item">
                            <div class="dedupe-match-row">
                                <a href="${match.issueUrl}" target="_blank" rel="noopener" class="dedupe-issue-link">${match.issueUrl}</a>
                                <span class="dedupe-score ${match.confidenceLevel}">${match.similarityPercentage}%</span>
                            </div>
                            ${match.issueTitle ? `<div class="dedupe-issue-title" style="font-weight: 500; color: #374151; font-size: 0.875rem; margin: 4px 0;">${match.issueTitle}</div>` : ''}
                        </li>
                        `).join('')}
                    </ul>
                </div>
                ` : ''}
            """
        
        # Insert the dedupe template before the closing </div>
        # We need to find the right place - after the validation-reasoning section
        
        # Look for the validation-reasoning conditional block
        validation_pattern = r"(\$\{issue\.validationReasoning\s*\?\s*`[\s\S]*?`\s*:\s*''\})"
        validation_match = re.search(validation_pattern, html_content)
        
        if validation_match:
            # Insert after the validation reasoning block
            insert_pos = validation_match.end()
            html_content = html_content[:insert_pos] + dedupe_template + html_content[insert_pos:]
        else:
            # Fallback: insert before the closing </div> of issue-content
            # Find the issue-content closing div
            issue_content_pattern = r'(<div class="issue-content">[\s\S]*?)(</div>\s*`\s*;)'
            issue_content_match = re.search(issue_content_pattern, html_content)
            if issue_content_match:
                insert_pos = issue_content_match.start(2)
                html_content = html_content[:insert_pos] + dedupe_template + html_content[insert_pos:]
        
        # Also modify the issueCard.className to add 'has-dedupe' class
        # Find: issueCard.className = `issue-card severity-${issue.severity}`;
        class_pattern = r"(issueCard\.className\s*=\s*`)issue-card severity-\$\{issue\.severity\}(`)"
        class_replacement = r"\1issue-card severity-${issue.severity}${issue.dedupeMatches && issue.dedupeMatches.length > 0 ? ' has-dedupe' : ''}\2"
        html_content = re.sub(class_pattern, class_replacement, html_content)
        
        # Add inline style for yellow background when has dedupe matches
        # This ensures the style is applied regardless of CSS specificity
        # Find the line after className assignment and add style assignment
        style_injection = """
                    if (issue.dedupeMatches && issue.dedupeMatches.length > 0) {
                        issueCard.style.backgroundColor = '#fef9c3';
                        issueCard.style.borderLeftColor = '#ca8a04';
                        issueCard.style.borderLeftWidth = '4px';
                    }"""
        
        # Find where to insert - after the className line
        class_line_pattern = r"(issueCard\.className\s*=\s*`[^`]+`\s*;)"
        class_line_match = re.search(class_line_pattern, html_content)
        if class_line_match:
            insert_pos = class_line_match.end()
            html_content = html_content[:insert_pos] + style_injection + html_content[insert_pos:]
        
        return html_content
    
    def _clean_issue_title(self, title: str, issue_url: str) -> str:
        """
        Clean up issue title by removing redundant URL prefix and markdown table.
        
        Args:
            title: The original issue title (may start with rdar://...)
            issue_url: The issue URL to remove from the title
            
        Returns:
            Cleaned title without the redundant URL prefix and markdown table.
        """
        if not title:
            return ''
        
        # Remove the issue URL from the beginning of the title
        # e.g., "rdar://166255866 ## Avoid copying..." -> "## Avoid copying..."
        cleaned = title
        
        # Remove rdar://NNNNNN prefix (but keep ## header)
        issue_id_pattern = r'^rdar://\d+\s*'
        cleaned = re.sub(issue_id_pattern, '', cleaned)
        
        # Remove everything from "| Field" onwards (markdown table)
        # e.g., "## Title | Field | Value |..." -> "## Title"
        table_pattern = r'\s*\|\s*Field.*$'
        cleaned = re.sub(table_pattern, '', cleaned, flags=re.DOTALL)
        
        # Remove leading/trailing whitespace
        cleaned = cleaned.strip()
        
        return cleaned
    
    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        return (
            text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )


def generate_annotated_report(
    original_report_path: Path,
    dedupe_results: Dict[str, Dict[str, Any]],
    output_path: Path
) -> None:
    """
    Convenience function to generate an annotated report.
    
    Args:
        original_report_path: Path to the original HTML report.
        dedupe_results: Dictionary mapping issue IDs to their match results.
        output_path: Path for the output HTML file.
    """
    generator = AnnotatedReportGenerator()
    generator.generate(original_report_path, dedupe_results, output_path)
