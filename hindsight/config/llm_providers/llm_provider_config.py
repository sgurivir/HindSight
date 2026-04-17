from typing import Dict, Any, Optional
import os
import json
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from hindsight.utils.log_util import get_logger

# Load environment variables from .env file
load_dotenv()

logger = get_logger(__name__)


class Credentials(BaseModel):
    """Legacy Credentials model for backward compatibility."""
    api_key: Optional[str] = None
    # AWS Bedrock specific fields
    region_name: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None


class LLMProviderConfig(BaseModel):
    """
    Configuration for LLM providers.
    
    Supports two authentication modes:
    1. Simple flat structure with 'credentials' and 'project-credentials' string fields
    2. Legacy nested Credentials object for backward compatibility
    
    Token Selection Priority (for aws_bedrock with Apple endpoints):
    1. project-credentials (FloodGate token) - highest priority
    2. credentials (OAuth/OIDC token)
    3. AppleConnect auto-refresh (fallback when neither provided)
    """
    api_end_point: str
    model: str
    llm_provider_type: str = Field(..., alias="llm_provider_type")
    
    # Simple string fields for flat configuration format
    credentials: Optional[str] = None  # OAuth/OIDC token
    project_credentials: Optional[str] = Field(None, alias="project-credentials")  # FloodGate project token
    
    # Legacy nested credentials object (for backward compatibility)
    legacy_credentials: Optional[Credentials] = Field(None, alias="credentials_object")
    
    # AWS Bedrock specific fields (optional)
    region_name: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None

    class Config:
        populate_by_name = True  # Allow both alias and field name

    def __init__(self, **kwargs):
        # Handle backward compatibility: if 'credentials' is a dict, convert to legacy format
        if 'credentials' in kwargs and isinstance(kwargs['credentials'], dict):
            # Legacy nested credentials format
            legacy_creds = kwargs.pop('credentials')
            kwargs['credentials_object'] = legacy_creds
            # Extract api_key from nested credentials for the flat field
            if 'api_key' in legacy_creds:
                kwargs['credentials'] = legacy_creds.get('api_key')
        
        super().__init__(**kwargs)
        
        provider_type = kwargs.get('llm_provider_type', '')
        
        if provider_type == 'aws_bedrock':
            # Load from environment if not provided in config
            # Priority: project-credentials > credentials > environment variables

            # Load project-credentials from environment if not provided
            if not self.project_credentials:
                env_project_token = os.getenv("FLOODGATE_PROJECT_TOKEN", "").strip()
                if env_project_token:
                    object.__setattr__(self, 'project_credentials', env_project_token)
                    logger.debug("Loaded project-credentials from FLOODGATE_PROJECT_TOKEN environment variable")

            # Load credentials from environment if not provided
            if not self.credentials:
                env_credentials = (
                    os.getenv("ANTHROPIC_API_KEY", "").strip() or
                    os.getenv("CREDENTIALS", "").strip() or
                    os.getenv("AWS_API_KEY", "").strip()
                )
                if env_credentials:
                    object.__setattr__(self, 'credentials', env_credentials)
                    logger.debug("Loaded credentials from environment variable")

            # Also load AWS credentials if needed for direct AWS access
            if not self.access_key_id or self.access_key_id in ["YOUR_AWS_ACCESS_KEY", "your_access_key_here"]:
                object.__setattr__(self, 'access_key_id', os.getenv("AWS_ACCESS_KEY_ID"))

            if not self.secret_access_key or self.secret_access_key in ["YOUR_AWS_SECRET_KEY", "your_secret_key_here"]:
                object.__setattr__(self, 'secret_access_key', os.getenv("AWS_SECRET_ACCESS_KEY"))

            if not self.region_name:
                object.__setattr__(self, 'region_name', os.getenv("AWS_REGION", "us-east-1"))
    
    def get_api_key(self) -> Optional[str]:
        """
        Get the API key for authentication.
        
        For aws_bedrock with Apple endpoints, follows priority:
        1. project_credentials (FloodGate token)
        2. credentials (OAuth/OIDC token)
        3. None (will trigger AppleConnect auto-refresh)
        
        Returns:
            str: API key/token if available, None otherwise
        """
        # For FloodGate mode, project_credentials takes priority
        if self.project_credentials and self.project_credentials.strip():
            return self.project_credentials.strip()
        
        # Fall back to credentials
        if self.credentials and self.credentials.strip():
            return self.credentials.strip()
        
        # Check legacy credentials object
        if self.legacy_credentials and self.legacy_credentials.api_key:
            return self.legacy_credentials.api_key
        
        return None
    
    def is_floodgate_mode(self) -> bool:
        """
        Check if using FloodGate project token authentication.
        
        Returns:
            bool: True if project-credentials is provided
        """
        return bool(self.project_credentials and self.project_credentials.strip())


class ProviderConfigLoader:
    # Base directory for LLM provider configuration JSON files
    BASE_CONFIG_DIR = os.path.join(
        os.path.dirname(__file__),
        "llm_providers_data" # This directory will contain the actual .json files
    )

    @classmethod
    def load_config(cls, provider_name: str) -> Optional[LLMProviderConfig]:
        """
        Loads and validates a specific LLM provider's configuration.
        """
        config_file_path = os.path.join(cls.BASE_CONFIG_DIR, f"{provider_name}.json")

        if not os.path.exists(config_file_path):
            logger.warning(f"LLM provider config file not found: {config_file_path}")
            return None

        try:
            with open(config_file_path, 'r', encoding='utf-8') as f:
                raw_config = json.load(f)


            return LLMProviderConfig(**raw_config)
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {config_file_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error loading config for {provider_name}: {e}")
            return None
