from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal

# --- Base Tool Parameter Schema ---
class BaseToolParams(BaseModel):
    """Base class for tool parameters (currently unused, placeholder)."""
    pass

# --- Specific Tool Parameter Schemas ---

class PrSummaryParams(BaseToolParams):
    """Parameters for the github_pr_summary tool."""
    repo_name: Optional[str] = Field(None, description="The GitHub repository name in 'owner/repo' format. If None, use default or memory.")
    pr_number: int = Field(..., description="The pull request number.")

class CherryPickParams(BaseToolParams):
    """Parameters for the github_request_cherry_pick tool."""
    target_branch: str = Field(..., description="The target branch to cherry-pick the PR into.")
    pr_number: int = Field(..., description="The source Pull Request number to cherry-pick.")
    repo_name: Optional[str] = Field(None, description="The GitHub repository name ('owner/repo') where the PR exists and the issue should be created. If None, use default or memory.")

class CreatePRParams(BaseToolParams):
    """Parameters for the github_create_pr tool."""
    repo_name: Optional[str] = Field(None, description="The GitHub repository name ('owner/repo'). If None, use default or memory.")
    base_branch: str = Field(..., description="The target branch the changes should be pulled into (e.g., 'main', 'develop').")
    head_branch: str = Field(..., description="The source branch containing the changes (e.g., 'feature/my-new-feature').")
    title: str = Field(..., description="The title for the pull request.")
    body: str = Field(..., description="The description body for the pull request (supports Markdown).")

class JiraCreateIssueParams(BaseToolParams):
    """Parameters for the jira_create_issue tool."""
    project_key: Optional[str] = Field(None, description="The Jira project key (e.g., 'PROJ'). If None, use default or memory.")
    summary: str = Field(..., description="The summary or title for the new Jira issue.")
    description: str = Field(..., description="The main description for the Jira issue (supports Jira wiki markup)." )
    issue_type: str = Field("Task", description="The type of issue to create (e.g., 'Task', 'Bug', 'Story'). Default is 'Task'.")
    assignee_id: Optional[str] = Field(None, description="The Jira Account ID of the user to assign the issue to.")
    labels: Optional[List[str]] = Field(None, description="A list of labels to add to the issue.")
    priority: Optional[str] = Field(None, description="The priority name (e.g., 'High', 'Medium', 'Lowest').")
    # custom_fields: Optional[Dict[str, Any]] = Field(None, description="Dictionary of custom field IDs and their values.") # Example for future use

class JiraUpdateStatusParams(BaseToolParams):
    """Parameters for the jira_update_status tool."""
    issue_key: str = Field(..., description="The key of the Jira issue to update (e.g., 'PROJ-123').")
    transition_name: str = Field(..., description="The exact name of the workflow transition to perform (e.g., 'Start Progress', 'Resolve Issue', 'Close Issue').")
    comment: Optional[str] = Field(None, description="Optional comment to add during the transition.")
    # resolution_name: Optional[str] = Field(None, description="Resolution name (e.g., 'Done', 'Fixed'). Required for some transitions.") # Example

class FeedbackSummaryParams(BaseToolParams):
    """Parameters for the jira_summarize_feedback tool."""
    jira_project_key: Optional[str] = Field(None, description="The Jira project key to aggregate feedback from. If None, use default or memory.")
    days_back: int = Field(30, description="How many days back to look for feedback items.")

class NotifyParams(BaseToolParams):
    """Parameters for the schedule_reminder tool."""
    mention_or_user: str = Field(..., description="The user or topic to be reminded about (can be @mention text or name).")
    delay_minutes: int = Field(..., description="The delay in minutes before the reminder is sent.")
    reminder_text: Optional[str] = Field(None, description="Optional specific text for the reminder message.")

class DemoParams(BaseToolParams):
    """Parameters for the record_demo_schedule tool (mock)."""
    schedule_details: str = Field(..., description="Text describing the desired date and time for the demo.")

class OctopusDeployParams(BaseToolParams):
    """Parameters for the octopus_trigger_deployment tool."""
    project_name: Optional[str] = Field(None, description="Name of the Octopus project. If None, use default or memory.")
    environment_name: Optional[str] = Field(None, description="Name of the Octopus environment. If None, use default or memory.")
    space_name: Optional[str] = Field(None, description="Octopus space name. If None, use default or memory.")
    release_version: Optional[str] = Field(None, description="Optional specific release version. If None, deploys latest.")
    tenant_name: Optional[str] = Field(None, description="Optional tenant name.")
    comments: Optional[str] = Field("Deployment triggered via ChatOps Bot", description="Optional comments.")


class PerplexitySearchParams(BaseToolParams):
    """Parameters for the perplexity_search tool."""
    query: str = Field(..., description="The search query for Perplexity AI.")

class SuggestActionsParams(BaseToolParams):
    """Parameters for the suggest_next_actions tool."""
    context_hint: Optional[str] = Field(None, description="Optional hint about the user's current goal.")

class ManagePreferencesParams(BaseToolParams):
    """Parameters for the manage_preferences tool."""
    action: Literal['set', 'get', 'list', 'digest'] = Field(..., description="Action: set, get, list preferences, or toggle digest (on/off).")
    pref_name: Optional[str] = Field(None, description="Preference name (e.g., 'default_repo', 'digest_timezone', 'github_login'). Required for 'set'/'get'.")
    pref_value: Optional[str] = Field(None, description="Value for preference. Required for 'set'. For digest, use 'on' or 'off'.")

class ManageAliasesParams(BaseToolParams):
    """Parameters for the manage_aliases tool."""
    action: Literal['set', 'delete', 'list'] = Field(..., description="Action: set, delete, or list command aliases.")
    alias_name: Optional[str] = Field(None, description="Alias name (single word). Required for 'set'/'delete'.")
    command_string: Optional[str] = Field(None, description="Full command string alias expands to. Required for 'set'.")

class ExplainParams(BaseToolParams):
    """Parameters for the explain_item tool."""
    item_identifier: str = Field(..., description="Identifier for item (e.g., 'PR 123', 'JIRA-456', 'owner/repo#123').")

# --- Tool Definition for Gemini ---
TOOL_CONFIG = {
    # DevOps Tools
    "github_pr_summary": {"name": "github_pr_summary", "description": "Retrieves and summarizes a specific GitHub Pull Request.", "parameters": PrSummaryParams.model_json_schema()},
    "github_create_pr": {"name": "github_create_pr", "description": "Creates a new GitHub Pull Request.", "parameters": CreatePRParams.model_json_schema()},
    "github_request_cherry_pick": {"name": "github_request_cherry_pick", "description": "Creates a GitHub issue to request cherry-picking a specific GitHub PR to a target branch.", "parameters": CherryPickParams.model_json_schema()},
    "jira_create_issue": {"name": "jira_create_issue", "description": "Creates a new issue (Task, Bug, Story) in a Jira project.", "parameters": JiraCreateIssueParams.model_json_schema()},
    "jira_update_status": {"name": "jira_update_status", "description": "Updates the status of a Jira issue by performing a workflow transition.", "parameters": JiraUpdateStatusParams.model_json_schema()},
    "jira_summarize_feedback": {"name": "jira_summarize_feedback", "description": "Fetches recent feedback from Jira and generates an AI summary.", "parameters": FeedbackSummaryParams.model_json_schema()},
    "octopus_trigger_deployment": {"name": "octopus_trigger_deployment", "description": "Triggers a deployment in Octopus Deploy.", "parameters": OctopusDeployParams.model_json_schema()},
    # Utility Tools
    "schedule_reminder": {"name": "schedule_reminder", "description": "Schedules a reminder message for the user.", "parameters": NotifyParams.model_json_schema()},
    "record_demo_schedule": {"name": "record_demo_schedule", "description": "Records the user's intent to schedule a demo (mock).", "parameters": DemoParams.model_json_schema()},
    "perplexity_search": {"name": "perplexity_search", "description": "Performs a web search using Perplexity AI.", "parameters": PerplexitySearchParams.model_json_schema()},
    # New UX/Meta Tools
    "suggest_next_actions": {"name": "suggest_next_actions", "description": "Suggests relevant next commands based on user context.", "parameters": SuggestActionsParams.model_json_schema()},
    "manage_preferences": {"name": "manage_preferences", "description": "Manages user preferences (defaults, digest, timezone, linked accounts).", "parameters": ManagePreferencesParams.model_json_schema()},
    "manage_aliases": {"name": "manage_aliases", "description": "Manages user-defined command aliases.", "parameters": ManageAliasesParams.model_json_schema()},
    "explain_item": {"name": "explain_item", "description": "Fetches details about a GitHub PR or Jira issue and provides an AI explanation.", "parameters": ExplainParams.model_json_schema()},
    # Internal tools (not usually directly called by LLM)
     "get_assigned_prs": {"name": "get_assigned_prs", "description": "Internal: Retrieves open pull requests for a user.", "parameters": {"user_github_login": {"type": "string"}}},
     "get_assigned_jira_issues": {"name": "get_assigned_jira_issues", "description": "Internal: Retrieves open Jira issues for a user.", "parameters": {"user_jira_identifier": {"type": "string"}}},
     "get_jira_issue_details": {"name": "get_jira_issue_details", "description": "Internal: Fetches full details for a Jira issue.", "parameters": {"issue_key": {"type": "string"}}},
     "send_user_digest": {"name": "send_user_digest", "description": "Internal: Sends the daily digest proactively.", "parameters": {"user_id": {"type": "string"}}}, # Minimal params needed for func lookup
}
"""Dictionary defining tools for Gemini's function calling feature.
Keys are tool names, values are dictionaries containing:
- name: Tool name (string).
- description: Description for the LLM (string).
- parameters: JSON schema derived from the Pydantic model for the tool's parameters.
Includes both user-facing and internal tools.
"""

# Prepare the list of tools in the format Gemini expects (filter out internal tools)
GEMINI_USABLE_TOOLS_LIST = [
    v for k, v in TOOL_CONFIG.items()
    if k not in ["get_assigned_prs", "get_assigned_jira_issues", "get_jira_issue_details", "send_user_digest", "prepare_cherry_pick_request"] # Hide internal/intermediate tools
]
"""List of tool definitions formatted for the Gemini API's `tools` parameter.
Filtered version of TOOL_CONFIG, excluding tools not intended for direct LLM invocation.
"""

# Map tool names to parameter models for validation
TOOL_PARAM_MODELS = {
    "github_pr_summary": PrSummaryParams,
    "github_create_pr": CreatePRParams,
    "github_request_cherry_pick": CherryPickParams,
    "jira_create_issue": JiraCreateIssueParams,
    "jira_update_status": JiraUpdateStatusParams,
    "jira_summarize_feedback": FeedbackSummaryParams,
    "schedule_reminder": NotifyParams,
    "record_demo_schedule": DemoParams,
    "octopus_trigger_deployment": OctopusDeployParams,
    "perplexity_search": PerplexitySearchParams,
    "suggest_next_actions": SuggestActionsParams,
    "manage_preferences": ManagePreferencesParams,
    "manage_aliases": ManageAliasesParams,
    "explain_item": ExplainParams,
}
"""Dictionary mapping tool names (strings) to their corresponding Pydantic parameter models.
Used for validating the arguments received from the Gemini function call before executing the tool.
"""