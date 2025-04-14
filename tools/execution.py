import logging
from typing import Dict, Any, Callable, Awaitable, Optional
from pydantic import ValidationError, BaseModel
from botbuilder.core import BotFrameworkAdapter # Passed for tools needing it
from botbuilder.schema import ConversationReference

from memory.interface import MemoryInterface
from .schemas import TOOL_PARAM_MODELS # Use this map for validation
# Import tool functions
from .github_tools import (
    get_pr_summary, prepare_cherry_pick_request, get_assigned_prs,
    github_create_pr, github_request_cherry_pick # Added new GitHub tools
)
from .jira_tools import (
    summarize_feedback, request_cherry_pick, get_assigned_jira_issues, 
    get_jira_issue_details, jira_create_issue, jira_update_status # Added jira_update_status
)
from .octopus_tools import trigger_octopus_deployment
from .perplexity_tools import search_perplexity
from .general_tools import (
    schedule_reminder, record_demo_schedule,
    suggest_next_actions, manage_preferences, manage_aliases, explain_item, send_user_digest
)

logger = logging.getLogger(__name__)

# Map tool names (from schema keys) to the actual async function implementations
TOOL_FUNCTIONS: Dict[str, Callable[..., Awaitable[Dict[str, Any]]]] = {
    # DevOps
    "github_pr_summary": get_pr_summary,
    "github_create_pr": github_create_pr,
    "github_request_cherry_pick": github_request_cherry_pick,
    "prepare_cherry_pick_request": prepare_cherry_pick_request, # Step 1 for cherry-pick validation (if using Jira path)
    "jira_create_issue": jira_create_issue,
    "jira_update_status": jira_update_status, # Added
    "jira_request_cherry_pick": request_cherry_pick,          # Step 2 for cherry-pick (Jira issue)
    "jira_summarize_feedback": summarize_feedback,
    "octopus_trigger_deployment": trigger_octopus_deployment,
    # Utility
    "schedule_reminder": schedule_reminder,
    "record_demo_schedule": record_demo_schedule,
    "perplexity_search": search_perplexity,
    # UX / Meta
    "suggest_next_actions": suggest_next_actions,
    "manage_preferences": manage_preferences,
    "manage_aliases": manage_aliases,
    "explain_item": explain_item,
    # Internal Tools
    "get_assigned_prs": get_assigned_prs,
    "get_assigned_jira_issues": get_assigned_jira_issues,
    "get_jira_issue_details": get_jira_issue_details,
    "send_user_digest": send_user_digest, # Called by scheduler
}
"""Maps tool names (strings) to their corresponding async function implementations."""

# Tools that need adapter/conversation_ref passed during execution
TOOLS_REQUIRING_CONTEXT = {"schedule_reminder", "manage_preferences", "send_user_digest"}
"""Set of tool names that require the BotFrameworkAdapter and ConversationReference for execution (e.g., for sending proactive messages)."""

# Tools that need requestor_name
TOOLS_REQUIRING_REQUESTOR = {"jira_request_cherry_pick", "github_create_pr", "github_request_cherry_pick", "jira_create_issue"} # Added jira_create_issue
"""Set of tool names that require the requesting user's name (`user_name`) for execution (e.g., for audit trails or mentions)."""

async def execute_tool(
    tool_name: str,
    tool_params: Dict[str, Any],
    memory: MemoryInterface,
    user_id: str,
    user_name: str, # For logging/audit/context
    adapter: Optional[BotFrameworkAdapter] = None, # Optional adapter for proactive tools
    conversation_ref: Optional[ConversationReference] = None # Optional conv ref for proactive tools
) -> Dict[str, Any]:
    """
    Validates parameters and executes the specified tool function based on its name.

    1. Finds the appropriate Pydantic model for the tool's parameters using `TOOL_PARAM_MODELS`.
    2. Validates the input `tool_params` against the Pydantic model.
    3. Finds the corresponding async tool function using the `TOOL_FUNCTIONS` map.
    4. Prepares arguments, injecting `memory`, `user_id`, and potentially `adapter`,
       `conversation_ref`, and `user_name` based on tool requirements.
    5. Calls the tool function with the validated and prepared arguments.
    6. Logs and returns the result dictionary from the tool function or an error dictionary.

    Args:
        tool_name: The name of the tool to execute (must be a key in `TOOL_FUNCTIONS`).
        tool_params: Dictionary of parameters received from the LLM function call.
        memory: An instance of the MemoryInterface for accessing user data/context.
        user_id: The unique identifier of the user requesting the tool execution.
        user_name: The display name of the user requesting the execution.
        adapter: Optional BotFrameworkAdapter instance, required by tools in `TOOLS_REQUIRING_CONTEXT`.
        conversation_ref: Optional ConversationReference, required by tools in `TOOLS_REQUIRING_CONTEXT`.

    Returns:
        A dictionary containing the result of the tool execution.
        On success: Typically `{"success": True, ...}` with tool-specific data.
        On failure: `{"success": False, "error": "...", "suggestion": "..."}`.
    """
    logger.info(f"Attempting to execute tool '{tool_name}' for user {user_id} ({user_name}) with params: {tool_params}")

    if tool_name not in TOOL_FUNCTIONS:
        logger.error(f"Unknown tool name requested: {tool_name}")
        # Return a more informative error message
        return {"success": False, "error": f"Tool '{tool_name}' is not available or not implemented correctly.", "suggestion": "Please check the available commands or contact support."}


    # --- Parameter Validation using Pydantic ---
    ParamModel: Optional[type[BaseModel]] = TOOL_PARAM_MODELS.get(tool_name)
    validated_params_dict = tool_params # Default to using raw params if no model

    if ParamModel:
        try:
            # Pydantic automatically handles converting dict to model instance
            # We don't strictly need the instance itself unless methods are called on it
            # Use model_validate to handle potential extra fields gracefully if needed, or just pass dict
            validated_model = ParamModel.model_validate(tool_params)
            validated_params_dict = validated_model.model_dump(exclude_unset=True) # Use validated dict, exclude Nones unless explicitly passed
            logger.debug(f"Validated parameters for {tool_name}: {validated_params_dict}")
        except ValidationError as e:
            logger.warning(f"Parameter validation failed for tool '{tool_name}': {e.errors()}")
            # Format error messages more clearly
            error_details = []
            for error in e.errors():
                field = error['loc'][0] if error.get('loc') else 'unknown field'
                msg = error['msg']
                error_details.append(f"Parameter '{field}': {msg}")

            error_msg = "Missing or invalid parameters required for this command."
            suggestion = f"Please provide valid values for the required parameters. Details: {'; '.join(error_details)}"
            return {"success": False, "error": error_msg, "suggestion": suggestion, "validation_errors": e.errors()}
        except Exception as e:
             logger.exception(f"Unexpected error during parameter validation for {tool_name}: {e}")
             return {"success": False, "error": "Internal error during parameter validation.", "suggestion": "Please report this issue."}

    # --- Execute Tool Function ---
    tool_function = TOOL_FUNCTIONS[tool_name]
    try:
        # Prepare arguments for the tool function using validated params directly
        # The tool function itself defines its arguments (e.g., repo_name: str, pr_number: int)
        # Pydantic validation ensures the keys exist and have the correct type
        tool_args = validated_params_dict.copy() # Start with validated params

        # Add context arguments required by specific tools
        tool_args["user_id"] = user_id
        # Pass memory object to tools that might need it (e.g., manage_preferences, manage_aliases, explain_item)
        # Or pass it to all tools for consistency if they handle the extra arg.
        # Let's pass it if the schema indicates it might be used (e.g., project_key lookup)
        # Better approach: Define explicitly which tools need memory, similar to context/requestor
        # For now, pass memory to all, assuming tool functions accept **kwargs or specific args
        tool_args["memory"] = memory 

        if tool_name in TOOLS_REQUIRING_CONTEXT:
            if adapter and conversation_ref:
                tool_args["adapter"] = adapter
                tool_args["conversation_ref"] = conversation_ref
            elif tool_name != "send_user_digest": # Digest gets context passed by scheduler caller
                 logger.warning(f"Tool '{tool_name}' might need adapter/conversation_ref but not provided.")
                 # Tool must handle potential None values if this happens

        if tool_name in TOOLS_REQUIRING_REQUESTOR:
             tool_args["user_name"] = user_name


        # Execute the async tool function with unpacked validated parameters and context
        result = await tool_function(**tool_args)

        # Log success/failure based on tool's own reporting
        if isinstance(result, dict) and result.get("success"):
            logger.info(f"Tool '{tool_name}' executed successfully for user {user_id}.")
        elif isinstance(result, dict) and "error" in result:
             logger.warning(f"Tool '{tool_name}' execution failed for user {user_id}: {result.get('error')}")
        else:
             logger.warning(f"Tool '{tool_name}' execution for user {user_id} returned unexpected result format: {result}")
             return {"success": False, "error": f"Tool '{tool_name}' returned an unexpected result.", "suggestion": "Check logs or contact support."}

        return result

    except TypeError as e:
        # Catch errors where the tool function was called with wrong/missing arguments
        logger.error(f"Argument mismatch calling tool '{tool_name}' for user {user_id}: {e}", exc_info=True)
        return {"success": False, "error": f"Internal error: Mismatched arguments when calling '{tool_name}'.", "suggestion": "This likely indicates a bug in the bot's tool integration. Please report this."}
    except Exception as e:
        logger.exception(f"Unhandled exception during execution of tool '{tool_name}' for user {user_id}: {e}")
        return {"success": False, "error": f"An unexpected error occurred while running the '{tool_name}' tool.", "suggestion": "Check bot logs or contact support."} 