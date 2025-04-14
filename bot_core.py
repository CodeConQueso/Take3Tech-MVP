import logging
import re
import json
from typing import Dict, Any, List, Set, Optional, Coroutine, Callable
from datetime import datetime, timezone # For audit timestamping
import asyncio # Added for background tasks
import traceback # Added for better error logging

from botbuilder.core import ActivityHandler, TurnContext, BotFrameworkAdapter, MessageFactory
from botbuilder.schema import Activity, ActivityTypes, ChannelAccount, ConversationReference, Activity, CardAction, ActionTypes, SuggestedActions
import google.generativeai as genai
from google.protobuf.json_format import MessageToDict
from fastapi import BackgroundTasks # For async tasks
from msgraph import GraphServiceClient # Added
from msgraph.generated.models.o_data_errors.o_data_error import ODataError # Added
from msgraph.generated.users.item.get_member_groups.get_member_groups_post_request_body import GetMemberGroupsPostRequestBody # Added
from azure.identity.aio import ClientSecretCredential # Added
from azure.core.exceptions import ClientAuthenticationError # Added
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type # Added for retries

# Local Imports
from utils.config import settings
from utils.helpers import (
    clean_message_text, send_adaptive_card, send_message, get_user_id,
    get_conversation_reference, create_adaptive_card_attachment
)
from utils.api_clients import get_gemini_client
from utils.audit_logger import audit
from memory import current_memory
from tools import GEMINI_USABLE_TOOLS_LIST, execute_tool # Use filtered list for LLM
import cards
from tools.general_tools import _update_digest_schedule

logger = logging.getLogger(__name__)

MAX_CONVERSATION_HISTORY = 6 # Not currently used, but placeholder
LONG_RUNNING_TOOLS = {"octopus_trigger_deployment", "jira_summarize_feedback", "perplexity_search", "explain_item"} # Define slow tools

# --- RBAC Helper ---
class PermissionDeniedError(Exception):
    """Custom exception for RBAC failures."""
    pass

# Cache for GraphServiceClient to avoid recreating it on every call
_graph_client_cache: Optional[GraphServiceClient] = None

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((ClientAuthenticationError, asyncio.TimeoutError, Exception)), # Retry on auth errors (might be transient) and general exceptions (network)
    reraise=True # Reraise the exception if all retries fail
)
async def _get_graph_client() -> Optional[GraphServiceClient]:
    """
    Creates and caches an asynchronous GraphServiceClient using app credentials.

    Uses configured Tenant ID, Client ID, and Client Secret from settings.
    Includes retry logic for transient authentication or network errors during initialization.

    Returns:
        An initialized GraphServiceClient instance, or None if configuration is missing.

    Raises:
        ClientAuthenticationError: If authentication fails after retries.
        Exception: For other unexpected errors during client creation after retries.
    """
    global _graph_client_cache
    if _graph_client_cache:
        return _graph_client_cache

    if not all([settings.MSGRAPH_TENANT_ID, settings.MSGRAPH_CLIENT_ID, settings.MSGRAPH_CLIENT_SECRET]):
        logger.error("Missing required Azure AD credentials (Tenant ID, Client ID, Client Secret) for Graph API access.")
        # Don't retry if config is missing, let it fail fast.
        # The decorator won't retry BaseExceptions like SystemExit or KeyboardInterrupt
        # If we want explicit no-retry on config, could raise a specific non-retried error here.
        return None # Or raise a specific ConfigurationError

    try:
        credential = ClientSecretCredential(
            tenant_id=settings.MSGRAPH_TENANT_ID,
            client_id=settings.MSGRAPH_CLIENT_ID,
            client_secret=settings.MSGRAPH_CLIENT_SECRET
        )
        scopes = [".default"]
        _graph_client_cache = GraphServiceClient(credentials=credential, scopes=scopes)
        logger.info("GraphServiceClient initialized successfully.")
        return _graph_client_cache
    except ClientAuthenticationError as auth_err:
        logger.error(f"Azure AD Authentication failed during client creation attempt: {auth_err}")
        raise # Reraise to allow retry decorator to catch it
    except Exception as e:
        logger.error(f"Failed to initialize GraphServiceClient during attempt: {e}")
        raise # Reraise general exceptions for retry

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((ODataError, asyncio.TimeoutError, Exception)), # Retry on Graph API errors and network issues
    reraise=True # Reraise the exception if all retries fail
)
async def _get_user_groups(user_aad_id: str) -> Set[str]:
    """
    Fetches the user's Azure AD security group memberships (Object IDs) using the Microsoft Graph API.

    Relies on `_get_graph_client` to obtain an authenticated client.
    Includes retry logic for transient Graph API or network errors during the group fetch.

    Args:
        user_aad_id: The Azure AD Object ID of the user.

    Returns:
        A set containing the Object IDs of the security groups the user is a member of.

    Raises:
        ODataError: If a Graph API error occurs after retries.
        Exception: If the Graph client is unavailable or another unexpected error occurs after retries.
    """
    logger.debug(f"Attempting to fetch AAD groups for user {user_aad_id}")
    graph_client = await _get_graph_client() # This call now has its own retry logic
    if not graph_client:
        # If _get_graph_client failed permanently after its retries, log and raise
        # A more specific exception type could be raised by _get_graph_client and caught here.
        logger.critical(f"Cannot fetch groups for user {user_aad_id}: Graph client unavailable after retries.")
        raise Exception(f"Graph client unavailable for user {user_aad_id}.") # Fail the operation

    try:
        request_body = GetMemberGroupsPostRequestBody(security_enabled_only=True)
        result = await graph_client.users.by_user_id(user_aad_id).get_member_groups.post(request_body)

        if result and result.value:
            group_ids = set(result.value)
            logger.info(f"Successfully fetched {len(group_ids)} security groups for user {user_aad_id}.")
            logger.debug(f"User {user_aad_id} groups: {group_ids}")
            return group_ids
        else:
            logger.info(f"User {user_aad_id} is not a member of any security groups or result was empty.")
            return set()

    except ODataError as odata_err:
        logger.warning(f"Graph API error during group fetch attempt for user {user_aad_id}: {odata_err.error.message if odata_err.error else 'Unknown ODataError'}. Retrying if applicable...")
        raise # Reraise for the decorator
    except Exception as e:
        logger.warning(f"Unexpected error during group fetch attempt for user {user_aad_id}: {e}. Retrying if applicable...")
        raise # Reraise for the decorator

async def _check_permissions(turn_context: TurnContext, tool_name: str):
    """
    Checks if the user has permission to run the specified tool based on RBAC configuration.

    - Skips check if RBAC_ENABLED is false.
    - Requires the user's AAD Object ID from the turn context.
    - Fetches user's AAD group memberships via `_get_user_groups`.
    - Compares user groups against required groups defined in settings (e.g., RBAC_DEPLOY_GROUPS).

    Args:
        turn_context: The current turn context, used to get user AAD ID.
        tool_name: The name of the tool being requested.

    Raises:
        PermissionDeniedError: If RBAC is enabled and the user lacks the required permissions,
                               or if the user's AAD ID is missing, or if group fetching fails.
    """
    user_teams_id = get_user_id(turn_context.activity) # Use helper which prefers AAD ID
    user_aad_id = turn_context.activity.from_property.aad_object_id # Get AAD ID explicitly

    if not settings.RBAC_ENABLED:
        audit.log("permission_check", user_id=user_teams_id, tool=tool_name, rbac_enabled=False, allowed=True)
        return

    if not user_aad_id:
        audit.log("permission_check", user_id=user_teams_id, tool=tool_name, rbac_enabled=True, allowed=False, reason="Missing AAD Object ID")
        logger.warning(f"RBAC check failed for user {user_teams_id}: User AAD Object ID not found in activity.")
        # Consider sending a specific message back to the user here
        raise PermissionDeniedError("Cannot verify identity for permissions. Your Azure AD user ID was not found.")

    try:
        # This call now benefits from the retry logic within _get_user_groups
        user_groups = await _get_user_groups(user_aad_id)
    except Exception as e:
        # This catch block now handles errors *after* retries in _get_user_groups have failed
        audit.log("permission_check", user_id=user_aad_id, tool=tool_name, rbac_enabled=True, allowed=False, reason=f"Failed to get user groups after retries: {e}")
        logger.error(f"Failed to get AAD groups for user {user_aad_id} after retries during permission check: {e}", exc_info=True)
        raise PermissionDeniedError("Error checking permissions after multiple attempts to retrieve your group memberships. Please try again later.")

    # --- Determine required groups based on tool and settings --- 
    required_groups = set()
    # Example: Load required groups from settings based on tool name
    # This structure assumes settings.RBAC_DEPLOY_GROUPS etc. are comma-separated strings in .env
    if tool_name in ["octopus_trigger_deployment"]: 
        required_groups = settings.RBAC_DEPLOY_GROUPS
    elif tool_name in ["some_admin_tool"]: # Add other tool-to-group mappings here
        required_groups = settings.RBAC_ADMIN_GROUPS
    # Add more specific tool permissions as needed
    # else: # Default case - maybe allow if no specific groups defined?
        # logger.debug(f"Tool '{tool_name}' does not have specific RBAC group requirements defined.")

    if not required_groups:
        audit.log("permission_check", user_id=user_aad_id, tool=tool_name, rbac_enabled=True, allowed=True, reason="No specific group required for this tool")
        logger.debug(f"Permission check passed for tool '{tool_name}' (no specific groups required). User: {user_aad_id}")
        return

    # Check for intersection between user's groups and required groups
    is_allowed = any(group_id in user_groups for group_id in required_groups)

    audit.log(
        "permission_check", 
        user_id=user_aad_id, 
        tool=tool_name, 
        rbac_enabled=True, 
        allowed=is_allowed, 
        user_groups=list(user_groups), # Log user's groups (consider privacy/size)
        required_groups=list(required_groups)
    )

    if not is_allowed:
        logger.warning(f"Permission DENIED for user {user_aad_id} to execute tool '{tool_name}'. User Groups: {user_groups}. Required Groups: {required_groups}.")
        raise PermissionDeniedError(f"You do not have permission to execute the '{tool_name}' command. Required group membership is missing.")
    else:
        logger.info(f"Permission GRANTED for user {user_aad_id} to execute tool '{tool_name}'.")


# --- Card Generator Helper Class/Functions ---
class CardGenerator:
    """Utility class to generate Adaptive Cards based on tool results."""
    async def generate_card_for_result(self, tool_name: str, result: Dict[str, Any], original_query: str | None) -> Dict | None:
        """
        Generates the appropriate Adaptive Card based on the tool execution result.

        Maps tool names to specific card creation functions in the `cards` module.
        Handles both success and error results.

        Args:
            tool_name: The name of the tool that was executed.
            result: The dictionary returned by the tool's execution function.
            original_query: The user's original query, used for context in error cards.

        Returns:
            A dictionary representing the JSON structure of an Adaptive Card, or None if no specific card is mapped.
        """
        card_json = None
        if isinstance(result, dict) and result.get("success"):
             # --- Success Cards ---
             if tool_name == "github_pr_summary": card_json = cards.create_pr_summary_card_from_data(result)
             elif tool_name == "jira_request_cherry_pick": card_json = cards.create_jira_issue_card(result)
             elif tool_name == "jira_summarize_feedback": card_json = cards.create_feedback_summary_card(result)
             elif tool_name == "octopus_trigger_deployment": card_json = cards.create_deployment_card(result)
             elif tool_name == "perplexity_search": card_json = cards.create_perplexity_card(result)
             elif tool_name == "suggest_next_actions": card_json = cards.create_suggestions_card(result.get("suggestions", []))
             elif tool_name == "manage_preferences": card_json = cards.create_pref_list_card(result.get("preferences", {})) if result.get("action") == "list" else cards.create_confirmation_card("Preference Update", result.get("message", "Done."))
             elif tool_name == "manage_aliases": card_json = cards.create_alias_list_card(result.get("aliases", {})) if result.get("action") == "list" else cards.create_confirmation_card("Alias Update", result.get("message", "Done."))
             elif tool_name == "explain_item": card_json = cards.create_explanation_card(result)
             elif tool_name in ["schedule_reminder", "record_demo_schedule"]: card_json = cards.create_confirmation_card(f"{tool_name.replace('_', ' ').title()} Success", result.get("message", "Done."), {"Details": result.get("details_recorded") or result.get("scheduled_time_utc")})
             elif tool_name == "prepare_cherry_pick_request": card_json = cards.create_confirmation_card("Validation OK", "Source/Target validated.", {k:v for k,v in result.items() if k != 'success'}) # Likely internal
             else: card_json = cards.create_confirmation_card(f"{tool_name.replace('_', ' ').title()} Completed", result.get("message", "Success"), {k:v for k,v in result.items() if k not in ['success', 'message']})
        elif isinstance(result, dict) and "error" in result: card_json = cards.create_tool_error_card(tool_name, result, original_query)
        else: logger.error(f"Tool '{tool_name}' returned unexpected result: {result}"); card_json = cards.create_error_card("Unexpected Tool Result", f"Tool '{tool_name}' result format issue.")
        return card_json

# --- Background Task Worker ---
async def run_tool_and_notify(
    adapter: BotFrameworkAdapter,
    # Pass conversation_id instead of the whole ref, which might be large/complex
    conversation_id: str,
    initial_activity_id: str | None,
    tool_name: str, tool_params: dict,
    # Pass memory instance if needed inside the tool execution itself
    # memory: Any, # Removed if tool executor doesn't need the whole instance
    user_id: str, user_name: str, original_query: str
    ):
    """
    Executes a potentially long-running tool in the background and sends a proactive message with the result.

    Intended to be run via FastAPI's BackgroundTasks.
    Retrieves the conversation reference from memory using the conversation_id.
    Executes the specified tool using `execute_tool`.
    Generates a result card using `CardGenerator`.
    Sends the card as a proactive message back to the original conversation using the adapter.

    Args:
        adapter: The BotFrameworkAdapter instance for sending proactive messages.
        conversation_id: The ID of the conversation to send the result to.
        initial_activity_id: The ID of the placeholder message activity (optional, for potential updates).
        tool_name: The name of the tool to execute.
        tool_params: Parameters for the tool.
        user_id: The ID of the user who initiated the request.
        user_name: The name of the user who initiated the request.
        original_query: The original user query text.
    """
    logger.info(f"Background task starting for tool '{tool_name}' user {user_id} targeting conv {conversation_id}")
    start_time = datetime.now(timezone.utc)
    card_generator = CardGenerator()

    # --- Retrieve Conversation Reference --- 
    # Get the memory instance (assuming current_memory is globally accessible or passed)
    # This might need adjustment based on how background tasks access shared resources
    memory_store = current_memory 
    conversation_ref: Optional[ConversationReference] = None
    try:
        conversation_ref = await memory_store.get_conversation_reference(conversation_id)
        if not conversation_ref:
            # Handle case where reference expired or couldn't be retrieved
            logger.error(f"Failed to retrieve conversation reference for {conversation_id} in background task. Cannot send proactive message.")
            # Log the tool result status even if we can't notify
            try:
                # Execute tool anyway to log its outcome
                tool_result = await execute_tool(tool_name=tool_name, tool_params=tool_params, memory=memory_store, user_id=user_id, user_name=user_name) 
                result_status = "success" if isinstance(tool_result, dict) and tool_result.get("success") else "failed"
                audit.log("background_tool_result_no_notify", user_id, tool=tool_name, params=tool_params, result_status=result_status, reason="ConvRef missing")
            except Exception as tool_err_logged:
                 audit.log("background_tool_result_no_notify", user_id, tool=tool_name, params=tool_params, result_status="critical_failure", error=str(tool_err_logged), reason="ConvRef missing")
            return # Stop processing this task
    except Exception as mem_err:
        logger.error(f"Error retrieving conversation reference {conversation_id} from memory: {mem_err}", exc_info=True)
        # Potentially log tool failure here too, as we can't proceed
        audit.log("background_tool_result_no_notify", user_id, tool=tool_name, params=tool_params, result_status="critical_failure", error=f"Memory error retrieving ConvRef: {mem_err}", reason="Memory error")
        return

    # --- Execute Tool --- 
    try:
        # Pass retrieved reference ONLY if execute_tool needs it (likely doesn't)
        tool_result = await execute_tool(tool_name=tool_name, tool_params=tool_params, memory=memory_store, user_id=user_id, user_name=user_name)
        end_time = datetime.now(timezone.utc); duration = (end_time - start_time).total_seconds()
        result_status = "success" if isinstance(tool_result, dict) and tool_result.get("success") else "failed"
        audit_detail = {"tool": tool_name, "params": tool_params, "result_status": result_status, "duration_seconds": duration}
        if result_status == "failed": audit_detail["error"], audit_detail["suggestion"] = tool_result.get("error"), tool_result.get("suggestion")
        audit.log("background_tool_result", user_id, **audit_detail)
    except Exception as bg_err:
        end_time = datetime.now(timezone.utc); duration = (end_time - start_time).total_seconds()
        logger.exception(f"Unhandled exception in background task for tool '{tool_name}': {bg_err}")
        tool_result = {"error": f"Critical error during background execution of {tool_name}.", "suggestion": "Check logs."}
        audit.log("background_tool_result", user_id, tool=tool_name, params=tool_params, result_status="critical_failure", error=str(bg_err), duration_seconds=duration)

    # --- Generate Result Card --- 
    card_json = None
    try: card_json = await card_generator.generate_card_for_result(tool_name, tool_result, original_query)
    except Exception as card_err: logger.exception(f"Error generating result card in background task: {card_err}"); card_json = cards.create_error_card("Card Generation Error", f"Failed to display results for {tool_name}.", str(card_err))
    if not card_json: card_json = cards.create_error_card("Result Error", f"Failed to generate result card for {tool_name}.")

    # --- Send Proactive Message --- 
    async def proactive_callback(proactive_turn_context: TurnContext):
        result_activity = Activity(type=ActivityTypes.message, attachments=[create_adaptive_card_attachment(card_json)])
        # TODO: Update the original placeholder message if initial_activity_id was provided?
        # This requires storing the initial_activity_id alongside the conv_ref or passing it differently.
        # Example: await proactive_turn_context.update_activity(result_activity)
        try: await proactive_turn_context.send_activity(result_activity); logger.info(f"Sent proactive result card for {tool_name} to conv {conversation_ref.conversation.id}")
        except Exception as send_error:
            logger.error(f"Failed to send result card activity via proactive message: {send_error}", exc_info=True)
            # Attempt to send plain text as fallback
            try:
                 status_msg = f"Task '{tool_name}' completed. Status: {'Success' if tool_result.get('success') else 'Failed'}. Unable to display card."
                 await proactive_turn_context.send_activity(status_msg)
            except Exception as fallback_send_error:
                 logger.error(f"Failed even to send plain text fallback message: {fallback_send_error}")

    try: 
        # Use the retrieved conversation_ref
        await adapter.continue_conversation(conversation_ref, proactive_callback, app_id=settings.MICROSOFT_APP_ID)
    except Exception as e: 
        logger.error(f"Error initiating proactive message for background task result (conv: {conversation_id}): {e}", exc_info=True)


# --- Main Bot Class ---
class TeamsGeminiBot(ActivityHandler):
    """
    Core bot logic handler for the Teams Gemini ChatOps Bot.

    Inherits from ActivityHandler and overrides methods to process incoming activities.
    Integrates Gemini for intent understanding, tool execution (sync/async),
    RBAC via MS Graph, memory management (context, prefs, aliases, conv refs),
    Adaptive Card generation, and proactive messaging.

    Attributes:
        adapter (BotFrameworkAdapter): The adapter used for communication.
        gemini_client: The initialized Google Gemini client.
        card_generator (CardGenerator): Instance for creating Adaptive Cards.
    """

    def __init__(self, adapter: BotFrameworkAdapter):
         """Initializes the TeamsGeminiBot."""
         self.adapter = adapter
         self.gemini_client = get_gemini_client()
         self.card_generator = CardGenerator()
         if not self.adapter: raise ValueError("Adapter instance required.")
         if not self.gemini_client: logger.critical("Gemini client not initialized.")
         logger.info("TeamsGeminiBot Enhanced initialized.")

    async def on_turn(self, turn_context: TurnContext, background_tasks: BackgroundTasks | None = None):
        """
        Main entry point for processing an incoming activity (turn) from the adapter.

        - Stores the conversation reference for potential proactive messages.
        - Checks for and processes pending daily digest schedules for the user.
        - Routes the activity to specific handlers based on type (message, conversationUpdate).

        Args:
            turn_context: The context object for the current turn.
            background_tasks: FastAPI BackgroundTasks instance, passed down for potential async operations.
        """
        user_id = None
        conv_ref = None
        adapter_for_reschedule = self.adapter # Use the instance adapter

        # Store conversation reference on message activities for potential proactive use
        if turn_context.activity.type == ActivityTypes.message:
             user_id = get_user_id(turn_context.activity)
             conv_ref = get_conversation_reference(turn_context.activity)
             if conv_ref and conv_ref.conversation and conv_ref.conversation.id:
                 try:
                     # Use the new dedicated storage method
                     await current_memory.store_conversation_reference(conv_ref)
                 except Exception as e:
                     logger.error(f"Failed to store conversation reference for {conv_ref.conversation.id}: {e}", exc_info=True)
             else:
                 logger.warning(f"Could not get a valid conversation reference from incoming activity: {turn_context.activity.id}")

             # --- Check for Pending Digest Schedule --- 
             if user_id: # Ensure we have user ID before checking memory
                 try:
                     is_pending = await current_memory.get_user_value(user_id, "digest_pending_schedule", False)
                     is_enabled = await current_memory.get_user_value(user_id, "digest_enabled", False)
                     
                     if is_pending and is_enabled and conv_ref and adapter_for_reschedule:
                         logger.info(f"Found pending digest schedule for user {user_id}. Attempting to schedule now.")
                         # Call the schedule update function with the current context
                         # This will overwrite the pending flag on success
                         await _update_digest_schedule(user_id, True, current_memory, adapter_for_reschedule, conv_ref)
                         # We don't need to explicitly delete the pending flag here, 
                         # _update_digest_schedule should do it upon successful scheduling.
                     elif is_pending and not is_enabled:
                          # If digest was disabled while pending, clear the flag
                          logger.info(f"Clearing pending digest schedule flag for user {user_id} as digest is disabled.")
                          await current_memory.delete_user_data(user_id, "digest_pending_schedule")
                 except Exception as e:
                     logger.error(f"Error checking/handling pending digest schedule for user {user_id}: {e}", exc_info=True)

        # Route activity using default handler, passing background_tasks if available
        if turn_context.activity.type == ActivityTypes.message:
             await self.on_message_activity(turn_context, background_tasks)
        elif turn_context.activity.type == ActivityTypes.conversation_update:
             if turn_context.activity.members_added:
                 await self.on_members_added_activity(turn_context.activity.members_added, turn_context)
        else:
             logger.debug(f"Received unhandled activity type: {turn_context.activity.type}")
             await super().on_turn(turn_context) # Use default ActivityHandler for others

    async def on_members_added_activity(self, members_added: List[ChannelAccount], turn_context: TurnContext):
        """Handles the 'conversationUpdate' activity when members (including the bot) are added to a chat/team."""
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                user_id = member.aad_object_id or member.id # Prefer AAD ID
                audit.log("member_added", user_id=user_id, member_name=member.name)
                logger.info(f"Member added: {user_id} ({member.name})")
                about_card = cards.create_about_card()
                await send_adaptive_card(turn_context, about_card)

    async def on_message_activity(self, turn_context: TurnContext, background_tasks: BackgroundTasks | None = None):
        """
        Handles incoming message activities (user text, card submissions).

        - Logs the incoming command.
        - Checks if the activity is a card action (e.g., messageBack, retry).
        - If it's text, cleans it and expands aliases.
        - Handles built-in commands like `/about`.
        - Otherwise, passes the processed query to `_process_user_intent`.

        Args:
            turn_context: The context object for the current turn.
            background_tasks: FastAPI BackgroundTasks instance for potential async operations.
        """
        activity = turn_context.activity; user_id = get_user_id(activity); user_name = activity.from_property.name or user_id
        original_text = activity.text or ""
        audit.log("command_received", user_id, raw_text=original_text, channel_id=activity.conversation.id)

        # --- Handle Card Actions (messageBack first) ---
        if activity.value and isinstance(activity.value, dict):
             msteams_data = activity.value.get("msteams", {})
             if msteams_data.get("type") == "messageBack":
                 original_text = msteams_data.get("text", "").strip()
                 logger.info(f"Processing messageBack as text: '{original_text}'")
                 if not original_text: return # Ignore empty
             else: # Handle other submits (retry, cancel)
                 action = activity.value.get("action")
                 audit.log("card_action_received", user_id, action=action, data=activity.value)
                 if action == "retry_request": 
                     retry_query = activity.value.get("original_query")
                     if retry_query: await self._process_user_intent(turn_context, user_id, user_name, retry_query, retry_query, background_tasks)
                     else: await send_message(turn_context, "Cannot retry: Original query missing.")
                 elif action == "cancel_request": await send_message(turn_context, "Request cancelled.")
                 else: await send_message(turn_context, f"Unknown action '{action}'.")
                 return 

        if not original_text: return

        # --- Alias Expansion & Command Routing ---
        cleaned_text = clean_message_text(activity); command_word = cleaned_text.split(' ')[0] if cleaned_text else ""
        processed_text = cleaned_text
        if command_word and not command_word.startswith('/'): # Expand non-slash commands
            aliases: Dict[str, str] = await current_memory.get_user_value(user_id, "command_aliases", {})
            if command_word in aliases:
                expansion = aliases[command_word]; rest_of_command = cleaned_text[len(command_word):].strip()
                processed_text = f"{expansion} {rest_of_command}".strip()
                logger.info(f"Expanded alias '{command_word}' to '{processed_text}'"); audit.log("alias_expanded", user_id, alias=command_word, expansion=processed_text)

        if not processed_text: return 

        if processed_text.lower() == "/about": await send_adaptive_card(turn_context, cards.create_about_card()); return

        await self._process_user_intent(turn_context, user_id, user_name, processed_text, original_text, background_tasks)

    async def _process_user_intent(self, turn_context: TurnContext, user_id: str, user_name: str, user_query: str, original_query: str, background_tasks: BackgroundTasks | None = None):
        """
        Core intent processing logic.

        - Sends a typing indicator.
        - Formats user memory/context for the LLM prompt.
        - Calls the Gemini API with the user query, context, system prompt, and available tools.
        - Handles the Gemini response:
            - If a tool call is requested: Checks permissions (`_check_permissions`), then executes the tool
              synchronously or asynchronously (`run_tool_and_notify`) based on `LONG_RUNNING_TOOLS`.
            - If a text response is generated: Sends the text back to the user.
            - If the prompt was blocked: Sends a safety message.
        - Sends the final result (card or text) or error message to the user.

        Args:
            turn_context: The current turn context.
            user_id: The ID of the user making the request.
            user_name: The name of the user making the request.
            user_query: The processed user query (after alias expansion).
            original_query: The original user query text (before alias expansion).
            background_tasks: FastAPI BackgroundTasks instance for async tool dispatch.
        """
        if not self.gemini_client: await cards.send_error_card(turn_context,"AI unavailable."); audit.log("intent_error", user_id, error="Gemini unavailable"); return
        await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        logger.info(f"Processing intent: '{user_query}'")
        start_time = datetime.now(timezone.utc)
        try:
            user_data = await current_memory.get_user_data(user_id); memory_context = self._format_memory_for_llm(user_data)
            system_prompt = ( # Updated prompt
                 "You are a helpful ChatOps assistant in Microsoft Teams. Identify the correct tool (like github_pr_summary, octopus_trigger_deployment, suggest_next_actions, manage_preferences, manage_aliases, explain_item) and parameters from the user request. "
                 "Use context first for missing parameters. If still missing, ask user. "
                 "Use 'suggest_next_actions' for general help requests. Use 'manage_preferences' for settings/digest. Use 'manage_aliases' for aliases. Use 'explain_item' for explain requests. "
                 "Adhere to schemas. Be concise. Do not execute harmful requests."
                 f"

User Context:
{memory_context}"
             )
            response = await self.gemini_client.generate_content_async(f"{system_prompt}

User Request: {user_query}", tools=GEMINI_USABLE_TOOLS_LIST)
            llm_duration = (datetime.now(timezone.utc) - start_time).total_seconds(); audit.log("llm_call_completed", user_id, query=user_query, duration_seconds=llm_duration)

            if not response.parts: raise ValueError("AI response empty.")
            response_part = response.parts[0]
            if response.prompt_feedback and response.prompt_feedback.block_reason: # Safety check
                 reason = response.prompt_feedback.block_reason.name; logger.warning(f"Gemini blocked prompt. Reason: {reason}")
                 audit.log("llm_refusal", user_id, query=user_query, reason=reason); await cards.send_error_card(turn_context, f"AI safety block ({reason}).", "Rephrase request."); return

            if hasattr(response_part, "function_call"): # Tool Call
                fc = response_part.function_call; tool_name = fc.name; tool_params = MessageToDict(fc.args) if fc.args else {}
                logger.info(f"LLM requests tool: {tool_name} with params: {tool_params}"); audit.log("llm_tool_request", user_id, query=user_query, tool=tool_name, params=tool_params)
                await _check_permissions(turn_context, tool_name) # RBAC Check
                
                # Get conversation ID *before* potentially dispatching to background
                conversation_id = turn_context.activity.conversation.id
                if not conversation_id:
                    logger.error("Cannot process tool request: Conversation ID missing from activity.")
                    await send_message(turn_context, "Sorry, couldn't process that request due to a technical issue (missing conversation ID).")
                    return

                if tool_name in LONG_RUNNING_TOOLS and background_tasks:
                    logger.info(f"Dispatching async tool: {tool_name} for conv {conversation_id}"); audit.log("tool_dispatch_async", user_id, tool=tool_name, params=tool_params)
                    placeholder_card = cards.create_confirmation_card(f"⏳ Processing: {tool_name.replace('_',' ').title()}", "Running in background...", {"Status": "Queued"})
                    sent_activity = await send_adaptive_card(turn_context, placeholder_card); initial_activity_id = sent_activity.id if sent_activity else None
                    
                    # Pass conversation_id to the background task
                    background_tasks.add_task(run_tool_and_notify, 
                                              adapter=self.adapter, 
                                              conversation_id=conversation_id, # Pass ID
                                              initial_activity_id=initial_activity_id, 
                                              tool_name=tool_name, 
                                              tool_params=tool_params, 
                                              # memory=current_memory, # Remove if not needed
                                              user_id=user_id, 
                                              user_name=user_name, 
                                              original_query=original_query)
                    return
                else: # Sync Execution
                    logger.info(f"Executing sync tool: {tool_name}"); audit.log("tool_dispatch_sync", user_id, tool=tool_name, params=tool_params)
                    tool_start = datetime.now(timezone.utc)
                    # Pass adapter/ref only if needed by sync tools
                    conversation_ref = await current_memory.get_conversation_reference(conversation_id) # Get ref if needed
                    tool_result = await execute_tool(tool_name=tool_name, tool_params=tool_params, memory=current_memory, user_id=user_id, user_name=user_name, adapter=self.adapter, conversation_ref=conversation_ref)
                    tool_duration = (datetime.now(timezone.utc) - tool_start).total_seconds()
                    result_status = "success" if isinstance(tool_result, dict) and tool_result.get("success") else "failed"; audit_detail = {"tool": tool_name, "params": tool_params, "result_status": result_status, "duration_seconds": tool_duration}
                    if result_status == "failed": audit_detail["error"], audit_detail["suggestion"] = tool_result.get("error"), tool_result.get("suggestion")
                    audit.log("sync_tool_result", user_id, **audit_detail)
                    card_json = await self.card_generator.generate_card_for_result(tool_name, tool_result, original_query)
                    if card_json: await send_adaptive_card(turn_context, card_json)
                    else: logger.error(f"Failed card gen for sync tool {tool_name}"); await send_message(turn_context, "Finished, but couldn't display results.")
            elif hasattr(response_part, "text"): # Text Response
                 text_response = response_part.text; logger.info(f"LLM text response: {text_response}")
                 audit.log("llm_text_response", user_id, query=user_query, response=text_response); await send_message(turn_context, text_response)
            else: raise ValueError(f"Unexpected LLM response format: {response_part}")
        except PermissionDeniedError as e: await cards.send_error_card(turn_context, "Permission Denied", str(e)) # Specific catch
        except Exception as e: # Generic catch-all
             logger.exception(f"Error processing user intent: {e}"); audit.log("intent_processing_error", user_id, query=user_query, error=str(e))
             error_result = {"error": "Unexpected error processing request.", "suggestion": "Contact support if persists."}; error_card = cards.create_tool_error_card("Intent Processing", error_result, original_query)
             await send_adaptive_card(turn_context, error_card)

    def _format_memory_for_llm(self, user_data: Dict[str, Any]) -> str:
        """Formats relevant user memory items into a string for the LLM prompt context."""
        if not user_data: return "No relevant user context found."
        relevant_keys = ["last_github_repo", "last_pr_details", "last_jira_project", "last_cherry_pick_issue", "last_octopus_project", "last_octopus_environment"]
        context_lines = []
        for key in relevant_keys:
            value = user_data.get(key)
            if value:
                if isinstance(value, dict): value_str = ", ".join(f"{k}={v}" for k, v in value.items())
                else: value_str = str(value)
                if len(value_str) > 100: value_str = value_str[:100] + "..."
                context_lines.append(f"- {key.replace('_', ' ').title()}: {value_str}")
        return "\n".join(context_lines) if context_lines else "No specific context found." 