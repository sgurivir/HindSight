"""
Report generation service for Hindsight API
Generates HTML reports from database-stored analysis results
"""
from typing import Dict, Any, Optional
from uuid import UUID
from datetime import datetime

from ..db.repositories import AnalysisRepository, ResultsRepository, RepositoryRepository
from .report_generator import generate_html_report
from ..utils.log_util import get_logger

logger = get_logger(__name__)


async def generate_report_for_analysis(
    analysis_id: UUID,
    repository_id: UUID
) -> str:
    """
    Generate an HTML report for a completed analysis.
    
    This function fetches all analysis results from the database and generates
    a comprehensive HTML report with an expandable directory tree structure,
    similar to the CLI-generated reports.
    
    Args:
        analysis_id: UUID of the analysis
        repository_id: UUID of the parent repository
        
    Returns:
        HTML content as a string
        
    Raises:
        ValueError: If analysis is not found or not completed
        Exception: If report generation fails
    """
    try:
        # Fetch analysis metadata
        analysis = await AnalysisRepository.get_by_id(analysis_id)
        if not analysis:
            raise ValueError(f"Analysis {analysis_id} not found")
        
        # Verify analysis is completed
        if analysis['status'] != 'completed':
            raise ValueError(f"Analysis {analysis_id} is not completed (status: {analysis['status']})")
        
        # Verify analysis belongs to the repository
        if analysis['repository_id'] != repository_id:
            raise ValueError(f"Analysis {analysis_id} does not belong to repository {repository_id}")
        
        # Fetch repository metadata
        repository = await RepositoryRepository.get_by_id(repository_id)
        if not repository:
            raise ValueError(f"Repository {repository_id} not found")
        
        project_name = repository.get('name', 'Unknown Repository')
        
        logger.info(f"Generating report for analysis {analysis_id} of repository {project_name}")
        
        # Fetch all results from database
        all_issues = await _fetch_all_results(analysis_id)
        
        if not all_issues:
            logger.warning(f"No issues found for analysis {analysis_id}")
            # Still generate report with empty results
        
        logger.info(f"Fetched {len(all_issues)} issues for report generation")
        
        # Transform database results to report format
        transformed_issues = _transform_results_for_report(all_issues)
        
        # Generate HTML report using existing report generator
        # Pass None as output_file to generate HTML string instead of writing to file
        html_content = generate_html_report(
            transformed_issues,
            output_file=None,  # Return HTML string instead of writing to file
            project_name=project_name
        )
        
        logger.info(f"Successfully generated HTML report for analysis {analysis_id}")
        return html_content
        
    except ValueError:
        # Re-raise validation errors
        raise
    except Exception as e:
        logger.error(f"Error generating report for analysis {analysis_id}: {e}", exc_info=True)
        raise Exception(f"Failed to generate report: {str(e)}")


async def _fetch_all_results(analysis_id: UUID) -> list:
    """
    Fetch all results for an analysis from the database.
    
    Handles pagination internally to fetch all results regardless of count.
    
    Args:
        analysis_id: Analysis UUID
        
    Returns:
        List of all result dictionaries
    """
    all_results = []
    offset = 0
    limit = 1000  # Fetch in batches of 1000
    
    while True:
        results, total = await ResultsRepository.get_paginated_results(
            analysis_id,
            limit=limit,
            offset=offset
        )
        
        if not results:
            break
        
        all_results.extend(results)
        offset += len(results)
        
        # If we've fetched everything, break
        if offset >= total:
            break
        
        logger.debug(f"Fetched {offset}/{total} results for analysis {analysis_id}")
    
    return all_results


def _transform_results_for_report(results: list) -> list:
    """
    Transform database result format to report generator format.
    
    The report generator expects a specific format with certain field names.
    This function maps the database schema to the expected format.
    
    Args:
        results: List of result dictionaries from database
        
    Returns:
        List of transformed result dictionaries
    """
    transformed = []
    
    for result in results:
        # Map database fields to report format
        # Database schema: file_path, function_name, line_number, severity, 
        #                  issue_type, description, impact, potential_solution
        # Report expects: file_path, function, lines, kind, category, description,
        #                 impact, suggestion (or similar variations)
        
        transformed_result = {
            'file_path': result.get('file_path', ''),
            'file': result.get('file_path', ''),  # Fallback field name
            'function': result.get('function_name'),
            'function_name': result.get('function_name'),  # Keep both
            'lines': result.get('line_number'),
            'line_number': result.get('line_number'),  # Keep both
            'severity': result.get('severity', 'medium'),
            'kind': result.get('severity', 'medium'),  # Report uses 'kind' for severity
            'issue_type': result.get('issue_type'),
            'issueType': result.get('issue_type'),  # camelCase variant
            'category': result.get('issue_type'),
            'description': result.get('description', ''),
            'issue': result.get('description', ''),  # Alternative field name
            'impact': result.get('impact'),
            'Impact': result.get('impact'),  # Capitalized variant
            'potential_solution': result.get('potential_solution'),
            'suggestion': result.get('potential_solution'),  # Report uses 'suggestion'
            'Potential solution': result.get('potential_solution'),  # Space variant
            'potentialSolution': result.get('potential_solution'),  # camelCase variant
        }
        
        transformed.append(transformed_result)
    
    return transformed

