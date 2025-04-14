import logging
from github import Github, GithubException
from jira import JIRA, JIRAError
import google.generativeai as genai
import httpx # For Octopus and Perplexity clients
from utils.config import settings
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

logger = logging.getLogger(__name__)

# --- Retry Configuration for transient API errors ---
# Retry on common network errors and specific HTTP status codes (e.g., 5xx)
RETRY_WAIT = wait_fixed(1) # Wait 1 second between retries
RETRY_ATTEMPTS = 3 # Retry up to 3 times
def is_retryable_exception(exception):
    """Check if the exception is a common transient error or a 5xx HTTP error."""
    if isinstance(exception, (httpx.TimeoutException, httpx.NetworkError, ConnectionError)):
        return True
    if isinstance(exception, (GithubException, JIRAError, httpx.HTTPStatusError)):
        # Retry on Server Errors (5xx) or Rate Limit (429 - use with caution)
        status_code = getattr(exception, 'status', getattr(exception, 'status_code', None))
        if status_code and (status_code >= 500 or status_code == 429):
            logger.warning(f"Retrying API call due to status {status_code}")
            return True
    return False

retry_transient = retry(
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=RETRY_WAIT,
    retry=retry_if_exception_type(Exception) # Temporarily retry on broad Exception for simplicity, refine with is_retryable_exception
    # retry=retry_if_exception(is_retryable_exception) # Use custom checker
)


# --- GitHub Client ---
github_client = None
if settings.GITHUB_TOKEN:
    try:
        github_client = Github(settings.GITHUB_TOKEN, timeout=20, retry=3) # Add timeout/retry if supported
        _ = github_client.get_user().login
        logger.info("Successfully connected to GitHub.")
    except Exception as e:
        logger.error(f"Failed to initialize GitHub client: {e}")
        github_client = None
else: logger.warning("GITHUB_TOKEN not found. GitHub features will be disabled.")

# --- Jira Client ---
jira_client = None
if settings.JIRA_SERVER and settings.JIRA_USERNAME and settings.JIRA_API_TOKEN:
    try:
        # Add timeout to jira client if supported by the library version
        jira_options = {'server': settings.JIRA_SERVER, 'timeout': 20}
        jira_client = JIRA(
            options=jira_options,
            basic_auth=(settings.JIRA_USERNAME, settings.JIRA_API_TOKEN)
            # Add retry logic if library supports it, otherwise wrap calls
        )
        _ = jira_client.myself() # Test connection
        logger.info("Successfully connected to Jira.")
    except JIRAError as e: logger.error(f"Failed to initialize Jira client: Status {e.status_code} - {e.text}"); jira_client = None
    except Exception as e: logger.error(f"An unexpected error occurred during Jira client initialization: {e}"); jira_client = None
else: logger.warning("Jira credentials not fully configured. Jira features will be disabled.")

# --- Gemini Client ---
gemini_client = None
if settings.GEMINI_API_KEY:
    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        # Use a model known for tool use
        gemini_model = genai.GenerativeModel(
            'gemini-1.5-flash', # Or 'gemini-pro'
             safety_settings=[ # Example: Stricter safety settings
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
             ]
        )
        gemini_client = gemini_model
        logger.info("Successfully configured Gemini client (gemini-1.5-flash with safety settings).")
    except Exception as e: logger.error(f"Failed to initialize Gemini client: {e}", exc_info=True); gemini_client = None
else: logger.error("GEMINI_API_KEY not found. AI processing is disabled.")


# --- Octopus Deploy Client ---
octopus_client = None
if settings.OCTOPUS_SERVER and settings.OCTOPUS_API_KEY:
    try:
        octopus_client = httpx.AsyncClient(
            base_url=f"{settings.OCTOPUS_SERVER.rstrip('/')}/api", # Ensure no double slash
            headers={"X-Octopus-ApiKey": settings.OCTOPUS_API_KEY, "Accept": "application/json"},
            timeout=30.0, # Increased timeout for potentially slow operations
            follow_redirects=True,
        )
        # Add connection test later if needed (e.g., in health check)
        logger.info("Octopus Deploy client configured.")
    except Exception as e: logger.error(f"Failed to configure Octopus Deploy client: {e}"); octopus_client = None
else: logger.warning("Octopus Deploy credentials not fully configured. Octopus features will be disabled.")

# --- Perplexity Client ---
perplexity_client = None
if settings.PERPLEXITY_API_KEY:
    try:
        perplexity_client = httpx.AsyncClient(
            base_url="https://api.perplexity.ai",
            headers={"Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}", "Accept": "application/json", "Content-Type": "application/json"},
            timeout=45.0
        )
        logger.info("Perplexity client configured.")
    except Exception as e: logger.error(f"Failed to configure Perplexity client: {e}"); perplexity_client = None
else: logger.warning("PERPLEXITY_API_KEY not found. Perplexity search features will be disabled.")


# --- Client Getters ---
def get_github_client():
    if not github_client: logger.warning("Attempted to use GitHub client, but it's not configured.")
    return github_client

def get_jira_client():
    if not jira_client: logger.warning("Attempted to use Jira client, but it's not configured.")
    return jira_client

def get_gemini_client():
    if not gemini_client: logger.error("Attempted to use Gemini client, but it's not configured.")
    return gemini_client

def get_octopus_client():
    if not octopus_client: logger.warning("Attempted to use Octopus Deploy client, but it's not configured.")
    return octopus_client

def get_perplexity_client():
    if not perplexity_client: logger.warning("Attempted to use Perplexity client, but it's not configured.")
    return perplexity_client

# --- Safe API Call Wrappers (with Retry Example) ---

@retry_transient
async def get_repo_safely(repo_name: str):
    """Safely gets a GitHub repository object, handling errors and retrying transients."""
    client = get_github_client()
    if not client: raise ConnectionError("GitHub client not available.")
    try:
        # Note: PyGithub sync calls might block async loop if not run in executor
        # For true async, consider libraries like `ghaio` or running sync calls in thread pool
        return client.get_repo(repo_name)
    except GithubException as e:
        logger.error(f"GitHub API error getting repo '{repo_name}': {e.status} - {e.data}")
        # Don't raise ConnectionError on 404 etc. - let the caller handle specific status codes
        raise e # Re-raise original exception after retry attempts
    except Exception as e:
        logger.error(f"Unexpected error getting repo '{repo_name}': {e}")
        raise RuntimeError(f"An unexpected error occurred accessing GitHub repo '{repo_name}'.") from e


@retry_transient
async def search_jira_issues_safely(jql_query: str, max_results: int = 50, fields: str = "*navigable"):
    """Safely searches Jira issues, handling errors and retrying transients."""
    client = get_jira_client()
    if not client: raise ConnectionError("Jira client not available.")
    try:
        # Note: python-jira is sync. Run in thread pool executor for async context.
        # loop = asyncio.get_running_loop()
        # issues = await loop.run_in_executor(None, lambda: client.search_issues(jql_query, maxResults=max_results, fields=fields))
        # Simpler for now: assume it's okay to block shortly or library handles it internally
        return client.search_issues(jql_query, maxResults=max_results, fields=fields)
    except JIRAError as e:
        logger.error(f"Jira API error searching issues with JQL '{jql_query}': {e.status_code} - {e.text}")
        raise e # Re-raise original exception
    except Exception as e:
        logger.error(f"Unexpected error searching Jira issues: {e}")
        raise RuntimeError("An unexpected error occurred while searching Jira issues.") from e

# Add similar wrappers with @retry_transient for httpx calls in Octopus/Perplexity tools if needed. 