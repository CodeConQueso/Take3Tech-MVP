import os
import logging
import re # For parsing map strings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

def parse_map_string(map_string: str | None) -> dict[str, str]:
    """Parses comma-separated key:value strings into a dict."""
    if not map_string:
        return {}
    mapping = {}
    try:
        pairs = map_string.strip().split(',')
        for pair in pairs:
            if ':' not in pair: continue
            key, value = pair.split(':', 1)
            mapping[key.strip()] = value.strip()
    except Exception as e:
        logger.error(f"Error parsing map string '{map_string}': {e}")
    return mapping

class Settings:
    """Loads and stores application configuration from environment variables."""
    # Core Bot Framework
    MICROSOFT_APP_ID = os.getenv("MICROSOFT_APP_ID", "")
    MICROSOFT_APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "")

    # LLM
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    # GitHub
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    DEFAULT_GITHUB_REPO = os.getenv("DEFAULT_GITHUB_REPO")

    # Jira
    JIRA_SERVER = os.getenv("JIRA_SERVER")
    JIRA_USERNAME = os.getenv("JIRA_USERNAME")
    JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
    DEFAULT_JIRA_PROJECT_KEY = os.getenv("DEFAULT_JIRA_PROJECT_KEY")

    # Octopus Deploy
    OCTOPUS_SERVER = os.getenv("OCTOPUS_SERVER")
    OCTOPUS_API_KEY = os.getenv("OCTOPUS_API_KEY")
    DEFAULT_OCTOPUS_SPACE_NAME = os.getenv("DEFAULT_OCTOPUS_SPACE_NAME")
    DEFAULT_OCTOPUS_PROJECT_NAME = os.getenv("DEFAULT_OCTOPUS_PROJECT_NAME")
    DEFAULT_OCTOPUS_ENVIRONMENT_NAME = os.getenv("DEFAULT_OCTOPUS_ENVIRONMENT_NAME")

    # Perplexity
    PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

    # Deployment / URL
    BOT_BASE_URL = os.getenv("BOT_BASE_URL", "http://localhost:8000") # Default to 8000

    # Memory Store (Optional Redis)
    REDIS_URL = os.getenv("REDIS_URL") # If set, Redis will be used

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    AUDIT_LOG_FILE = os.getenv("AUDIT_LOG_FILE", "logs/audit.log") # Path for audit log

    # --- Security Settings ---
    # RBAC - Comma-separated AAD Group Object IDs
    RBAC_ENABLED = os.getenv("RBAC_ENABLED", "false").lower() == "true"
    # Example: Map tool name or category to required AAD group IDs
    RBAC_CONFIG = {
        "deployments": set(filter(None, os.getenv("RBAC_DEPLOY_GROUPS", "").split(','))),
        "admin": set(filter(None, os.getenv("RBAC_ADMIN_GROUPS", "").split(',')))
        # Add more mappings as needed (e.g., 'sensitive_read': {'group_id_3'})
    }
    # Rate Limiting (slowapi format: "requests/period")
    RATE_LIMIT_USER_DEFAULT = os.getenv("RATE_LIMIT_USER_DEFAULT", "100/minute")
    RATE_LIMIT_IP_DEFAULT = os.getenv("RATE_LIMIT_IP_DEFAULT", "500/minute") # Fallback if user ID fails
    # Example per-tool limits (applied if stricter than default)
    RATE_LIMITS_TOOL = {
        "octopus_trigger_deployment": os.getenv("RATE_LIMIT_DEPLOY", "5/minute"),
        "perplexity_search": os.getenv("RATE_LIMIT_PERPLEXITY", "30/minute"),
    }

    # --- Feature Settings ---
    # Webhooks
    GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
    OCTOPUS_WEBHOOK_SECRET = os.getenv("OCTOPUS_WEBHOOK_SECRET")

    # Proactive Messaging & Digests
    DEFAULT_USER_TIMEZONE = os.getenv("DEFAULT_USER_TIMEZONE", "UTC")

    # Webhook Target Mapping
    GITHUB_REPO_CHANNEL_MAP: dict[str, str] = parse_map_string(os.getenv("GITHUB_REPO_CHANNEL_MAP"))
    OCTOPUS_PROJECT_CHANNEL_MAP: dict[str, str] = parse_map_string(os.getenv("OCTOPUS_PROJECT_CHANNEL_MAP"))

    # Suggest Command Limits
    SUGGEST_MAX_PRS_TO_CHECK = int(os.getenv("SUGGEST_MAX_PRS_TO_CHECK", 5))
    SUGGEST_MAX_JIRA_TO_CHECK = int(os.getenv("SUGGEST_MAX_JIRA_TO_CHECK", 5))

    # MS Graph API Config (Needed for RBAC)
    MSGRAPH_TENANT_ID = os.getenv("MSGRAPH_TENANT_ID")
    MSGRAPH_CLIENT_ID = os.getenv("MSGRAPH_CLIENT_ID")
    MSGRAPH_CLIENT_SECRET = os.getenv("MSGRAPH_CLIENT_SECRET")


    # --- Validations ---
    REQUIRED_FOR_STARTUP = ["MICROSOFT_APP_ID", "MICROSOFT_APP_PASSWORD", "GEMINI_API_KEY"]
    missing_startup_vars = [var for var in REQUIRED_FOR_STARTUP if not getattr(self, var)]
    if missing_startup_vars:
        raise ValueError(f"Missing critical environment variables: {', '.join(missing_startup_vars)}. Please check your .env file.")

    # Log warnings for missing optional tool keys
    OPTIONAL_TOOL_VARS = {
        "GitHub": ["GITHUB_TOKEN"],
        "Jira": ["JIRA_SERVER", "JIRA_USERNAME", "JIRA_API_TOKEN"],
        "Octopus Deploy": ["OCTOPUS_SERVER", "OCTOPUS_API_KEY"],
        "Perplexity": ["PERPLEXITY_API_KEY"],
    }
    for tool, variables in OPTIONAL_TOOL_VARS.items():
        if not all(getattr(self, var) for var in variables):
            logger.warning(f"{tool} integration may be limited or disabled due to missing environment variables: {', '.join(v for v in variables if not getattr(self, v))}")

    if REDIS_URL:
        logger.info("REDIS_URL is set. Attempting to use Redis for memory store.")
    else:
        logger.info("REDIS_URL is not set. Using in-memory store (not recommended for production scaling).")

    if RBAC_ENABLED:
        logger.info("RBAC is ENABLED. Permissions will be checked based on configured groups.")
        # Check if Graph API credentials are provided if RBAC is enabled
        if not all([MSGRAPH_TENANT_ID, MSGRAPH_CLIENT_ID, MSGRAPH_CLIENT_SECRET]):
            logger.error("RBAC is enabled, but MS Graph API credentials (MSGRAPH_TENANT_ID, MSGRAPH_CLIENT_ID, MSGRAPH_CLIENT_SECRET) are missing. RBAC checks will fail.")
            # Optionally raise an error here if Graph is strictly required for RBAC
            # raise ValueError("Missing MS Graph API credentials required for RBAC.")
        elif not any(RBAC_CONFIG.values()):
            logger.warning("RBAC is enabled, but no RBAC groups are defined in RBAC_DEPLOY_GROUPS or RBAC_ADMIN_GROUPS. Access might be unintentionally blocked.")
    else:
        logger.warning("RBAC is DISABLED. All users can execute all commands.")

    if GITHUB_REPO_CHANNEL_MAP and not GITHUB_WEBHOOK_SECRET:
         logger.warning("GitHub channel mapping defined, but GITHUB_WEBHOOK_SECRET is missing. Webhooks will fail security checks.")
    # Add similar check for Octopus if secret is mandatory

settings = Settings() 