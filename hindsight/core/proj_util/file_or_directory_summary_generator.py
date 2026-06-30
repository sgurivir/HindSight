#!/usr/bin/env python3
# Created by Sridhar Gurivireddy
"""File / directory summary generator.

Provides a sync `get_summary_of_file(root, relative_path)` API that drives an
iterative LLM stage with the standard file-system tools, then extracts the
final `{"summary": "..."}` JSON.

Built on top of `hindsight.llm.SyncStageRunner` + `stage_file_summary`. Sync
on the outside (so existing callers in `git_simple_diff_analyzer` keep
working unchanged), async on the inside (one fresh `AsyncLLMClient` + event
loop per `get_summary_of_file` call, safe under `asyncio.to_thread`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from hindsight.core.constants import (
    DEFAULT_LLM_API_END_POINT,
    DEFAULT_LLM_MODEL,
    DEFAULT_MAX_TOKENS,
)
from hindsight.core.prompts.fallback_prompts import FALLBACK_FILE_SUMMARY_SYSTEM
from hindsight.llm.bedrock import LLMClientConfig
from hindsight.llm.stages import stage_file_summary
from hindsight.llm.sync_bridge import SyncStageRunner
from hindsight.llm.tools import ToolContext, build_default_registry
from hindsight.utils.config_util import (
    get_api_key_from_config,
    get_llm_provider_type,
    load_and_validate_config,
)
from hindsight.utils.directory_tree_util import DirectoryTreeUtil
from hindsight.utils.file_content_provider import FileContentProvider
from hindsight.utils.log_util import get_logger, setup_default_logging
from hindsight.utils.output_directory_provider import get_output_directory_provider

setup_default_logging()
logger = get_logger(__name__)


def load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_file = project_root / "hindsight" / "core" / "prompts" / "fileSummaryPrompt.md"
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            content = f.read()
        # Skip the title line.
        lines = content.split("\n")
        return "\n".join(lines[1:]).strip()
    except Exception as e:
        logger.warning(f"Could not load system prompt from {prompt_file}: {e}")
        return FALLBACK_FILE_SUMMARY_SYSTEM


def _load_user_prompt_template() -> Optional[str]:
    template_path = project_root / "hindsight" / "core" / "prompts" / "fileSummaryUserPrompt.md"
    try:
        return template_path.read_text(encoding="utf-8")
    except Exception:
        return None


class FileOrDirectorySummaryGenerator:
    """Generates English summaries of files using the async LLM stack."""

    def __init__(self, llm_provider: str, config: Dict[str, Any]):
        """
        Args:
            llm_provider: LLM provider type (kept for legacy CLI compatibility; only
                'aws_bedrock' is currently supported).
            config: Configuration dictionary containing LLM settings.
        """
        if llm_provider not in ("aws_bedrock",):
            raise ValueError(f"Unsupported LLM provider: {llm_provider}")

        self.llm_provider = llm_provider
        self.config = config

        self.api_key = get_api_key_from_config(config)
        if not self.api_key:
            raise ValueError(f"No API key available for provider: {llm_provider}")

        self._client_config = LLMClientConfig(
            api_url=config.get("api_end_point", DEFAULT_LLM_API_END_POINT),
            model=config.get("model", DEFAULT_LLM_MODEL),
            max_tokens=int(config.get("max_tokens", DEFAULT_MAX_TOKENS)),
            api_key=self.api_key,
        )

        # Cache tool registries per repo so repeated summaries reuse the
        # FileContentProvider + tree util instead of rebuilding them.
        self._registry_cache: Dict[str, Any] = {}

        logger.info(f"Initialized FileOrDirectorySummaryGenerator with provider: {llm_provider}")

    def _get_or_create_registry(self, repo_path: str):
        if repo_path in self._registry_cache:
            return self._registry_cache[repo_path]

        output_provider = get_output_directory_provider()
        if not output_provider.is_configured():
            output_provider.configure(repo_path, "/tmp/")
            logger.debug(
                f"Configured OutputDirectoryProvider with repo_path: {repo_path}, output_dir: /tmp/"
            )

        try:
            file_content_provider = FileContentProvider.get()
        except RuntimeError:
            try:
                file_content_provider = FileContentProvider.from_repo(repo_path)
            except Exception as e:
                logger.warning(f"Failed to create FileContentProvider: {e}")
                file_content_provider = None

        artifacts_dir = f"{output_provider.get_repo_artifacts_dir()}/code_insights"
        ignore_dirs = set(self.config.get("exclude_directories", []))

        ctx = ToolContext(
            repo_path=repo_path,
            file_content_provider=file_content_provider,
            artifacts_dir=artifacts_dir,
            directory_tree_util=DirectoryTreeUtil(),
            ignore_dirs=ignore_dirs,
        )
        registry = build_default_registry(ctx)
        self._registry_cache[repo_path] = registry
        return registry

    def get_summary_of_file(self, root: str, relative_path: str) -> str:
        """Generate a 2-3 line English summary of what a file does."""
        full_path = Path(root) / relative_path
        if not full_path.exists():
            return f"Error: File not found at {relative_path}"

        registry = self._get_or_create_registry(root)
        system_prompt = load_system_prompt()

        template = _load_user_prompt_template()
        if template:
            user_prompt = template.replace("{relative_path}", relative_path)
        else:
            user_prompt = (
                f"Please analyze the file '{relative_path}' and provide a 2-3 line summary of what it does.\n\n"
                "Use the available tools to read the file contents and understand its purpose. Focus on:\n"
                "1. What the file's primary function or purpose is\n"
                "2. Key components it contains (classes, functions, configurations, etc.)\n"
                "3. How it fits into the broader codebase\n\n"
                f"File to analyze: {relative_path}"
            )

        try:
            runner = SyncStageRunner(self._client_config)
            verdict = runner.run(stage_file_summary(system_prompt), user_prompt, tools=registry)
        except Exception as e:
            error_msg = f"Error generating summary for {relative_path}: {e}"
            logger.error(error_msg)
            return error_msg

        if not isinstance(verdict, dict) or "summary" not in verdict:
            logger.error(f"Failed to extract summary for {relative_path}; verdict={verdict!r}")
            return f"Error: Failed to generate summary for {relative_path}"

        summary = verdict["summary"]
        if not isinstance(summary, str):
            summary = str(summary)

        logger.info(f"Generated summary for {relative_path}: {len(summary)} characters")
        return summary.strip() or "Unable to generate summary from response."


def main():
    """Main entry point for CLI usage."""
    parser = argparse.ArgumentParser(
        description="Generate English summaries of files using LLM providers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Summarize a single file
  python -m hindsight.core.proj_util.file_or_directory_summary_generator --root /path/to/repo --config config.json --file src/main.py

  # Use AWS Bedrock provider
  python -m hindsight.core.proj_util.file_or_directory_summary_generator --root /path/to/repo --config bedrock_config.json --file README.md
        """,
    )

    parser.add_argument("--root", "-r", required=True, help="Path to the repository root directory")
    parser.add_argument("--config", "-c", required=True, help="Path to LLM configuration JSON file")
    parser.add_argument("--file", "-f", required=True, help="Relative path to the file to summarize (from root)")

    args = parser.parse_args()

    try:
        config = load_and_validate_config(args.config)
        llm_provider = get_llm_provider_type(config)
        generator = FileOrDirectorySummaryGenerator(llm_provider, config)
        summary = generator.get_summary_of_file(args.root, args.file)
        print(summary)
    except Exception as e:
        logger.error(f"Error: {e}")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
