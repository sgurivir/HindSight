import os
import platform
from dotenv import load_dotenv

from ..core.lang_util.cast_util import CASTUtil
from ..core.constants import DEFAULT_LLM_API_END_POINT, DEFAULT_LLM_MODEL

# Load environment variables from .env file
load_dotenv()

class ServerConfig:
    API_END_POINT: str = os.getenv("API_END_POINT", DEFAULT_LLM_API_END_POINT)
    MODEL: str = os.getenv("MODEL", DEFAULT_LLM_MODEL)
    CREDENTIALS: str = os.getenv("CREDENTIALS", "")

    # Auto-detect libclang path based on platform
    # Environment variable takes precedence, then platform-specific defaults
    @staticmethod
    def _get_default_libclang_path() -> str:
        return CASTUtil.get_libclang_path_from_platform()

    # Use environment variables for sensitive data
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    CLEANUP_CLONED_REPOS: bool = os.getenv("CLEANUP_CLONED_REPOS", "false").lower() == "true"
    MAX_REPO_SIZE_KB: int = int(os.getenv("MAX_REPO_SIZE_KB", "1000000"))  # Maximum repository size in KB
