"""
Report package for generating annotated HTML reports.

This package provides:
- AnnotatedReportGenerator: Class for generating annotated HTML reports
- generate_annotated_report: Convenience function for report generation
"""

from .html_generator import AnnotatedReportGenerator, generate_annotated_report

__all__ = [
    'AnnotatedReportGenerator',
    'generate_annotated_report',
]
