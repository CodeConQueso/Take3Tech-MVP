import logging
from typing import Dict, Any, Optional, List
from jira import JIRAError, JIRA # Import JIRA for type hint
from jira.resources import Issue # Added for type hint
import asyncio # For running sync jira calls in executor
import re
from datetime import datetime, timedelta, timezone

# Import schemas and memory interface
from .schemas import FeedbackSummaryParams, CherryPickParams, JiraCreateIssueParams, JiraUpdateStatusParams # Added UpdateStatus params
from memory.interface import MemoryInterface
# Import API clients and retry decorator
from utils.api_clients import get_jira_client, get_gemini_client, search_jira_issues_safely, retry_transient
from utils.config import settings
from utils.audit_logger import audit # Added for audit logging

logger = logging.getLogger(__name__)

MAX_FEEDBACK_ITEMS_FOR_SUMMARY = 50
MAX_CHARS_FOR_GEMINI_INPUT = 25000

# Helper to run sync Jira calls in thread pool
async def run_jira_sync(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

# --- Create Jira Issue --- 
@retry_transient
async def jira_create_issue_internal(jira_client_instance: JIRA, issue_dict: dict) -> Issue:
     """Internal function to create Jira issue with retry, runs sync in executor."""
     return await run_jira_sync(jira_client_instance.create_issue, fields=issue_dict)

async def jira_create_issue(
    project_key: Optional[str],
    summary: str,
    description: str,
    issue_type: str,
    assignee_id: Optional[str],
    labels: Optional[List[str]],
    priority: Optional[str],
    user_id: str,
    user_name: str,
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Creates a new issue in Jira.

    Args:
        project_key: Jira project key or None to use default/memory.
        summary: Title for the new issue.
        description: Description for the new issue (supports Jira wiki markup).
        issue_type: Type of issue (e.g., 'Task', 'Bug').
        assignee_id: Jira Account ID of the assignee, or None.
        labels: List of labels to add, or None.
        priority: Priority name (e.g., 'High'), or None.
        user_id: ID of the user creating the issue.
        user_name: Name of the user creating the issue.
        memory: Memory interface for resolving default project.

    Returns:
        Dict: Success dictionary with created issue details or error dictionary.
    """
    jira: Optional[JIRA] = get_jira_client()
    if not jira: return {"error": "Jira client not configured."}

    proj_key = project_key or await memory.get_user_value(user_id, "default_jira_project") or settings.DEFAULT_JIRA_PROJECT
    if not proj_key: return {"error": "Jira project key missing.", "suggestion": "Specify project key or set default."}

    logger.info(f"Executing tool: jira_create_issue in project {proj_key} by user {user_id} ({user_name})")

    # Construct issue fields dictionary
    issue_dict = {
        "project": {"key": proj_key},
        "summary": summary,
        "description": description, # Assumes description is already formatted if needed
        "issuetype": {"name": issue_type},
    }
    if assignee_id:
        issue_dict["assignee"] = {"accountId": assignee_id}
    if labels:
        issue_dict["labels"] = labels
    if priority:
        # Fetch available priorities for the project if needed, or assume name works
        # For simplicity, assume priority name is sufficient
        issue_dict["priority"] = {"name": priority}

    # Add more fields as needed (custom fields, components, fixVersions etc.)
    # Example custom field:
    # if custom_field_value:
    #     issue_dict["customfield_10001"] = custom_field_value

    try:
        created_issue: Issue = await jira_create_issue_internal(jira, issue_dict)
        logger.info(f"Successfully created Jira issue {created_issue.key} in project {proj_key}.")
        await memory.set_user_data(user_id, "last_jira_project", proj_key)
        await memory.set_user_data(user_id, "last_jira_issue", {"key": created_issue.key, "url": created_issue.permalink()})
        return {"success": True, "issue_key": created_issue.key, "issue_url": created_issue.permalink(), "message": f"Created Jira issue {created_issue.key}"}

    except JIRAError as e:
        logger.error(f"Jira API error creating issue in {proj_key}: {e.status_code} - {e.text}")
        # Provide more specific suggestions if possible (e.g., invalid field value)
        suggestion = f"Failed to create issue: {e.text[:100]}"
        if "assignee" in e.text.lower(): suggestion += " Check assignee ID."
        elif "priority" in e.text.lower(): suggestion += " Check priority name."
        elif "project key is invalid" in e.text.lower(): suggestion += " Check project key."
        audit.log("tool_failure", user_id=user_id, tool_name="jira_create_issue", error=f"{e.status_code} {e.text}", details=str(e.response.content if e.response else None))
        return {"error": f"Jira API Error ({e.status_code}).", "suggestion": suggestion}
    except Exception as e:
        logger.exception(f"Unexpected error creating Jira issue in {proj_key}: {e}")
        audit.log("tool_failure", user_id=user_id, tool_name="jira_create_issue", error=f"Unexpected error: {e}")
        return {"error": "Unexpected error creating Jira issue."}


async def summarize_feedback(
    jira_project_key: Optional[str],
    days_back: int,
    user_id: str,
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Summarizes recent feedback from Jira issues using an LLM.

    Fetches recent issues (e.g., bugs, feedback tasks) from the specified
    or default Jira project.
    Extracts relevant text (summary, description, comments).
    Sends the extracted text to Gemini for summarization.

    Args:
        jira_project_key: Jira project key or None to use memory/default.
        days_back: Number of days back to look for feedback issues.
        user_id: ID of the user making the request.
        memory: Memory interface for resolving default project.

    Returns:
        Dict: Success dictionary with the feedback summary or error dictionary.
    """
    jira = get_jira_client(); gemini = get_gemini_client()
    if not jira: return {"error": "Jira client not configured."}
    if not gemini: return {"error": "Gemini client not configured."}

    proj_key = jira_project_key or await memory.get_user_value(user_id, "default_jira_project") or settings.DEFAULT_JIRA_PROJECT
    if not proj_key: return {"error": "Jira project key missing.", "suggestion": "Specify project key or set default."}

    logger.info(f"Executing tool: summarize_feedback for project {proj_key}, {days_back} days back (User: {user_id})")

    try:
        # Define JQL query
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        # Example: Look for Bugs or specific feedback types created recently
        jql = f'project = "{proj_key}" AND issuetype in (Bug, "Feedback Request", Task) AND created >= "{cutoff_date}" ORDER BY created DESC'

        # Fetch issues (limit results to avoid overload)
        issues = await search_jira_issues_safely(jql, max_results=50, fields="summary,description,comment,created,reporter")

        if not issues:
            return {"success": True, "message": f"No recent feedback issues found in project {proj_key} in the last {days_back} days."}

        # Extract text for summarization (limit total length)
        feedback_text = ""
        MAX_SUMMARY_CHARS = 15000 # Limit context for LLM
        for issue in issues:
            issue_data = f"Issue: {issue['key']} ({issue['fields']['summary']}) - Reported: {issue['fields']['created'][:10]}\n"
            if issue["fields"].get("description"): issue_data += f"Description: {issue['fields']['description']}\n"
            if issue["fields"].get("comment", {}).get("comments"):
                issue_data += "Comments:\n"
                for comment in issue["fields"]["comment"]["comments"]:
                    issue_data += f"- {comment['body']}\n"
            issue_data += "---\n"

            if len(feedback_text) + len(issue_data) < MAX_SUMMARY_CHARS:
                feedback_text += issue_data
            else:
                remaining_len = MAX_SUMMARY_CHARS - len(feedback_text)
                if remaining_len > 100: # Add partial if space allows
                    feedback_text += issue_data[:remaining_len] + "... (truncated)"
                break # Stop adding more issues

        if not feedback_text:
             return {"success": True, "message": f"Could not extract text from recent feedback issues in {proj_key}."}

        # Call Gemini to summarize
        prompt = (
            f"You are an AI assistant summarizing recent user feedback from Jira for project {proj_key}.\n"
            f"Based *only* on the following text extracted from recent issues (descriptions, comments), provide a concise summary (3-5 bullet points) highlighting key themes, problems, or suggestions mentioned.\n"
            f"Focus on actionable insights or recurring topics. Mention issue keys for reference where appropriate.\n\n"
            f"Extracted Feedback Text:\n{feedback_text}\n\n"
            f"Summary of Recent Feedback (3-5 key bullet points):"
        )

        summary_response = await gemini.generate_content_async(prompt)
        summary = summary_response.text.strip() if summary_response.text else "Could not generate a summary."

        logger.info(f"Generated feedback summary for project {proj_key} (User: {user_id})")
        await memory.set_user_data(user_id, "last_jira_project", proj_key)
        return {"success": True, "project": proj_key, "days_back": days_back, "issues_analyzed": len(issues), "summary": summary}

    except JIRAError as e:
        logger.error(f"Jira API error summarizing feedback for {proj_key}: {e.status_code} - {e.text}")
        return {"error": f"Jira API Error ({e.status_code}).", "suggestion": f"Failed to search issues: {e.text[:100]}"}
    except Exception as e:
        logger.exception(f"Unexpected error summarizing Jira feedback for {proj_key}: {e}")
        return {"error": "Unexpected error summarizing Jira feedback."}


@retry_transient
async def create_jira_issue_internal(jira_client_instance: JIRA, issue_dict: dict) -> Any:
     """Internal function to create Jira issue with retry."""
     # Run sync call in executor
     return await run_jira_sync(jira_client_instance.create_issue, fields=issue_dict)


async def request_cherry_pick(
    project_key: Optional[str],
    source_ref: str, # Can be PR# or Commit SHA
    target_version: str, # Usually maps to a branch or release version
    repo_name: Optional[str], # Added for context
    user_id: str,
    user_name: str, # Added for assignee lookup/mention
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Creates a Jira issue to track a cherry-pick request.

    Uses the source reference, target version, and repository context to create
    a structured Jira issue (e.g., Task or Sub-task) in the specified or default project.
    Attempts to automatically assign the issue based on user preferences or defaults.

    Args:
        project_key: Jira project key or None to use memory/default.
        source_ref: Identifier for the source (e.g., 'PR 123', 'commit abcdef').
        target_version: The target version/branch for the cherry-pick.
        repo_name: Source repository name ('owner/repo') or None.
        user_id: ID of the user making the request.
        user_name: Name of the user making the request.
        memory: Memory interface for resolving defaults and user context.

    Returns:
        Dict: Success dictionary with Jira issue details or error dictionary.
    """
    jira: Optional[JIRA] = get_jira_client()
    if not jira: return {"error": "Jira client not configured."}
    logger.info(f"Executing tool: request_cherry_pick for user {user_id} ({user_name})")

    proj_key = project_key or await memory.get_user_value(user_id, "default_jira_project") or settings.DEFAULT_JIRA_PROJECT
    if not proj_key: return {"error": "Jira project key missing.", "suggestion": "Specify project key or set default."}

    # Construct issue details
    summary = f"Cherry-Pick Request: {source_ref} to {target_version}"
    description = (
        f"Request from: {user_name}\n"
        f"Source: {source_ref} {f'(Repo: {repo_name})' if repo_name else ''}\n"
        f"Target Version/Branch: {target_version}\n\n"
        f"Please cherry-pick the specified changes."
    )
    issue_type = "Task" # Or make configurable

    # Try to find assignee
    assignee_id = await memory.get_user_value(user_id, "jira_account_id") # Assuming user links their own ID

    try:
        issue_dict = {
            "project": {"key": proj_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type},
        }
        if assignee_id:
            issue_dict["assignee"] = {"accountId": assignee_id}

        # Call internal create issue tool/function
        created_issue: Issue = await create_jira_issue_internal(jira, issue_dict)

        await memory.set_user_data(user_id, "last_jira_project", proj_key)
        await memory.set_user_data(user_id, "last_cherry_pick_issue", created_issue.key)
        return {"success": True, "issue_key": created_issue.key, "issue_url": created_issue.permalink(), "summary": summary, "repo_name": repo_name, "commit_sha": source_ref, "target_branch": target_version}

    except JIRAError as e:
        logger.error(f"Jira API error creating cherry-pick issue in {proj_key}: {e.status_code} - {e.text}")
        return {"error": f"Jira API Error ({e.status_code}).", "suggestion": f"Failed to create issue: {e.text[:100]}"}
    except Exception as e:
        logger.exception(f"Unexpected error creating Jira cherry-pick issue: {e}")
        return {"error": "Unexpected error creating Jira issue."}


@retry_transient
async def get_assigned_jira_issues_internal(jira_client_instance: JIRA, jql_query: str, max_results: int, fields: str) -> List[Any]:
     """Internal fetch logic for assigned Jira issues with retry."""
     return await run_jira_sync(search_jira_issues_safely, jql_query, max_results=max_results, fields=fields)


async def get_assigned_jira_issues(
    user_jira_identifier: str, # Could be email or Account ID
    user_id: str, # For logging
    memory: MemoryInterface, # Unused
    **kwargs
) -> Dict[str, Any]:
    """
    (Internal) Fetches open Jira issues assigned to a specific user.

    Used primarily by the daily digest feature.
    Requires the user's Jira identifier (Account ID preferred, fallback to email).
    Queries the Jira API for open, assigned issues.

    Args:
        user_jira_identifier: Jira Account ID or email address.
        user_id: Internal user ID for logging.
        memory: Memory interface (unused).

    Returns:
        Dict: Success dictionary with a list of assigned issues or error dictionary.
    """
    jira: Optional[JIRA] = get_jira_client()
    if not jira: return {"error": "Jira client not configured."}
    logger.info(f"Fetching assigned Jira issues for identifier: {user_jira_identifier} (User ID: {user_id})")

    if not user_jira_identifier:
        return {"success": False, "error": "Jira identifier not provided.", "suggestion": "Link your Jira account using /pref set jira_account_id <ID>."}

    try:
        # Use JQL to find open issues assigned to the user
        # Assumes identifier is Account ID - adjust JQL if primarily using email
        jql = f'assignee = "{user_jira_identifier}" AND statusCategory != Done ORDER BY updated DESC'
        issues = await get_assigned_jira_issues_internal(
             jira, jql, settings.SUGGEST_MAX_JIRA_TO_CHECK, "summary,status,updated,issuetype"
        )

        assigned_issues = []
        for issue in issues:
            assigned_issues.append({
                "key": issue.key,
                "url": issue.permalink(),
                "summary": issue.fields.summary,
                "status": issue.fields.status.name,
                "type": issue.fields.issuetype.name,
                "updated": issue.fields.updated,
            })

        logger.info(f"Found {len(assigned_issues)} open assigned Jira issues for {user_jira_identifier}.")
        return {"success": True, "assigned_issues": assigned_issues}

    except JIRAError as e:
        logger.error(f"Jira API error searching assigned issues for {user_jira_identifier}: {e.status_code} - {e.text}")
        return {"error": "Jira API Error searching issues.", "suggestion": f"Status: {e.status_code}"}
    except Exception as e:
        logger.exception(f"Unexpected error searching assigned Jira issues for {user_jira_identifier}: {e}")
        return {"error": "Unexpected error searching Jira issues."}


@retry_transient
async def get_jira_issue_details_internal(jira_client_instance: JIRA, issue_key: str) -> Any:
     """Internal fetch logic for single Jira issue details."""
     # Fetch more fields for explanation
     fields="summary,description,comment,status,reporter,assignee,created,updated,priority,labels,issuetype,resolution"
     return await run_jira_sync(jira_client_instance.issue, issue_key, fields=fields)

async def get_jira_issue_details(
    issue_key: str,
    user_id: str,
    memory: MemoryInterface, # Unused
    **kwargs
) -> Dict[str, Any]:
    """
    (Internal) Fetches detailed information for a specific Jira issue.

    Used by the `explain_item` tool.
    Retrieves core fields (summary, description, status, assignee, etc.) and potentially comments.

    Args:
        issue_key: The Jira issue key (e.g., 'PROJ-123').
        user_id: Internal user ID for logging.
        memory: Memory interface (unused).

    Returns:
        Dict: Success dictionary with issue details or error dictionary.
    """
    jira: Optional[JIRA] = get_jira_client()
    if not jira: return {"error": "Jira client not configured."}
    logger.info(f"Fetching details for Jira issue: {issue_key} (User ID: {user_id})")

    if not issue_key or not re.match(r"^[A-Z][A-Z0-9]+-\d+$", issue_key):
        return {"error": f"Invalid Jira issue key format: '{issue_key}'.", "suggestion": "Use format like 'PROJECT-123'."}

    try:
        # Fetch issue details, including comments
        issue_data = await get_jira_issue_details_internal(jira, issue_key)
        # Simplify the returned data if necessary, especially large fields or comments
        # For now, return the raw structure from the client
        logger.info(f"Successfully fetched details for Jira issue {issue_key}.")
        return {"success": True, "issue_details": issue_data}

    except JIRAError as e:
        if e.status_code == 404:
            logger.warning(f"Jira issue {issue_key} not found.")
            return {"error": f"Jira issue '{issue_key}' not found.", "suggestion": "Please check the issue key."}
        else:
            logger.error(f"Jira API error fetching issue {issue_key}: {e.status_code} - {e.text}")
            return {"error": f"Jira API Error ({e.status_code}).", "suggestion": f"Failed to fetch issue: {e.text[:100]}"}
    except Exception as e:
        logger.exception(f"Unexpected error fetching Jira issue {issue_key}: {e}")
        return {"error": "Unexpected error fetching Jira issue details."}

# --- Update Jira Issue Status --- 
@retry_transient
async def jira_transition_issue_internal(jira_client_instance: JIRA, issue_key: str, transition_name: str, comment: Optional[str] = None, fields: Optional[dict] = None) -> bool:
    """Internal function to transition Jira issue status with retry."""
    # Fetch available transitions first to find the correct ID
    async def find_transition_id():
        transitions = await run_jira_sync(jira_client_instance.transitions, issue_key)
        for t in transitions:
            if t['name'].lower() == transition_name.lower():
                return t['id']
        return None
    
    transition_id = await find_transition_id()
    if not transition_id:
        logger.error(f"Transition '{transition_name}' not found for issue {issue_key}.")
        # Raise a specific error that can be caught by the caller
        raise ValueError(f"Transition '{transition_name}' not available for issue {issue_key}. Check available transitions.")

    # Run the transition in executor
    logger.debug(f"Attempting transition ID {transition_id} ('{transition_name}') on issue {issue_key}")
    await run_jira_sync(jira_client_instance.transition_issue, issue_key, transition_id, comment=comment, fields=fields)
    return True # Return True on success (no exception raised)

async def jira_update_status(
    issue_key: str,
    transition_name: str,
    comment: Optional[str],
    user_id: str,
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Updates the status of a Jira issue by performing a workflow transition.

    Finds the transition ID matching the provided transition name for the issue's workflow.
    Performs the transition, optionally adding a comment.

    Args:
        issue_key: The Jira issue key (e.g., 'PROJ-123').
        transition_name: The exact name of the workflow transition (e.g., 'Start Progress').
        comment: Optional comment to add during the transition.
        user_id: ID of the user requesting the status change.
        memory: Memory interface (unused).

    Returns:
        Dict: Success or error dictionary.
    """
    jira: Optional[JIRA] = get_jira_client()
    if not jira: return {"error": "Jira client not configured."}

    logger.info(f"Executing tool: jira_update_status for issue {issue_key} to '{transition_name}' by user {user_id}")

    if not issue_key or not re.match(r"^[A-Z][A-Z0-9]+-\d+$", issue_key):
        return {"error": f"Invalid Jira issue key format: '{issue_key}'.", "suggestion": "Use format like 'PROJECT-123'."}

    try:
        # Find the transition ID
        transitions = await jira.get_transitions(issue_key)
        transition_id = None
        available_transitions = []
        for t in transitions:
            available_transitions.append(t["name"])
            if t["name"].lower() == transition_name.lower():
                transition_id = t["id"]
                break

        if not transition_id:
            logger.warning(f"Transition '{transition_name}' not found for issue {issue_key}. Available: {available_transitions}")
            return {"error": f"Transition '{transition_name}' not found or not available for issue {issue_key}.", "suggestion": f"Available transitions: {', '.join(available_transitions)}"}

        # Perform the transition
        await jira.transition_issue(issue_key, transition_id, comment=comment)

        logger.info(f"Successfully transitioned Jira issue {issue_key} using '{transition_name}'.")
        return {"success": True, "message": f"Jira issue {issue_key} transitioned to status corresponding to '{transition_name}'."}

    except JIRAError as e:
        logger.error(f"Jira API error transitioning issue {issue_key}: {e.status_code} - {e.text}")
        suggestion = f"Failed to transition issue: {e.text[:100]}"
        if e.status_code == 404: suggestion = "Issue not found. Check the issue key."
        elif "transition is not valid" in e.text.lower(): suggestion = f"Transition '{transition_name}' is not valid for the current status. Check available transitions."
        return {"error": f"Jira API Error ({e.status_code}).", "suggestion": suggestion}
    except Exception as e:
        logger.exception(f"Unexpected error transitioning Jira issue {issue_key}: {e}")
        return {"error": "Unexpected error transitioning Jira issue."} 