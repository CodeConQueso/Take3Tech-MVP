import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Set, Literal
import re
import pytz # For timezone handling in digest scheduling
import json # For formatting explanation context

from botbuilder.core import BotFrameworkAdapter, TurnContext
from botbuilder.schema import Activity, ActivityTypes, ConversationReference # Added ConversationReference type hint
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger

from .schemas import NotifyParams, DemoParams, SuggestActionsParams, ManagePreferencesParams, ManageAliasesParams, ExplainParams
from memory.interface import MemoryInterface
from utils.scheduler import schedule_task, remove_job, scheduler
from utils.config import settings
from utils.api_clients import get_gemini_client
from .execution import execute_tool # To call internal tools

# Need helpers and card attachment for digest sending
from utils.helpers import create_adaptive_card_attachment, get_conversation_reference
# Avoid top-level card imports if possible to prevent cycles
# from cards.digest_card import create_digest_card

logger = logging.getLogger(__name__)

# Allowed preference keys definition
ALLOWED_PREFS = {
    "default_repo": {"type": str, "description": "Default GitHub repository (owner/repo)."},
    "default_jira_project": {"type": str, "description": "Default Jira project key."},
    "default_octopus_space": {"type": str, "description": "Default Octopus space name."},
    "default_octopus_project": {"type": str, "description": "Default Octopus project name."},
    "default_octopus_environment": {"type": str, "description": "Default Octopus environment name."},
    "digest_enabled": {"type": bool, "description": "Enable daily digest (true/false)."},
    "digest_timezone": {"type": str, "description": "Your timezone for daily digest (e.g., America/New_York)."},
    "github_login": {"type": str, "description": "Your GitHub username (for assignments)."},
    "jira_account_id": {"type": str, "description": "Your Jira Account ID (for assignments)."},
}


# --- Internal function for scheduled digest task ---
async def send_user_digest(
    user_id: str,
    memory: MemoryInterface,
    adapter: BotFrameworkAdapter,
    conversation_ref: ConversationReference,
    **kwargs
) -> Dict[str, Any]:
    """
    Generates and sends the daily digest to a specific user.

    Intended to be called by the scheduler.
    Fetches user's linked accounts (GitHub/Jira) from preferences.
    Calls internal tools to get assigned PRs and Jira issues.
    Formats the information into a digest card.
    Sends the card proactively using the provided adapter and conversation reference.

    Args:
        user_id: The ID of the user to send the digest to.
        memory: Memory interface for retrieving preferences and context.
        adapter: BotFrameworkAdapter instance for sending the proactive message.
        conversation_ref: ConversationReference for the target user chat.

    Returns:
        Dict: Success or error dictionary.
    """
    logger.info(f"Starting daily digest generation for user {user_id}")
    # Import memory and cards locally to avoid module-level circular dependencies
    from memory import current_memory
    import cards

    if not adapter or not conversation_ref:
         logger.error(f"Cannot send digest to user {user_id}: Missing adapter or conversation reference.")
         # Maybe try fetching ref from memory again? Risky.
         return

    digest_data = {"prs": [], "jira_issues": []}
    errors = []

    # --- Fetch Data using internal tools ---
    github_login = await current_memory.get_user_value(user_id, "github_login")
    jira_identifier = await current_memory.get_user_value(user_id, "jira_account_id")

    if github_login:
        try:
            pr_result = await execute_tool("get_assigned_prs", {"user_github_login": github_login}, current_memory, user_id, "Digest Bot")
            if pr_result.get("success"): digest_data["prs"] = pr_result.get("assigned_prs", [])
            else: errors.append(f"GitHub PRs: {pr_result.get('error', 'Failed')}")
        except Exception as e: logger.error(f"Digest: Error fetching assigned PRs for user {user_id}: {e}"); errors.append("GitHub PRs: Error fetching data.")
    else: errors.append("GitHub: Account not linked. Use `/pref set github_login <username>`")

    if jira_identifier:
        try:
            jira_result = await execute_tool("get_assigned_jira_issues", {"user_jira_identifier": jira_identifier}, current_memory, user_id, "Digest Bot")
            if jira_result.get("success"): digest_data["jira_issues"] = jira_result.get("assigned_issues", [])
            else: errors.append(f"Jira Issues: {jira_result.get('error', 'Failed')}")
        except Exception as e: logger.error(f"Digest: Error fetching assigned Jira for user {user_id}: {e}"); errors.append("Jira Issues: Error fetching data.")
    else: errors.append("Jira: Account not linked. Use `/pref set jira_account_id <id>`")

    # --- Format and Send Card ---
    digest_card = cards.create_digest_card(digest_data, errors)

    async def message_callback(proactive_turn_context: TurnContext):
        await proactive_turn_context.send_activity(
             Activity(type=ActivityTypes.message, attachments=[create_adaptive_card_attachment(digest_card)])
        )

    try:
        await adapter.continue_conversation(conversation_ref, message_callback, app_id=settings.MICROSOFT_APP_ID)
        logger.info(f"Successfully sent daily digest to user {user_id}")
    except Exception as e:
        logger.error(f"Error sending proactive digest to user {user_id}: {e}", exc_info=True)
        # Consider removing job or marking user if conversation ref consistently fails?


# --- Tool Implementations ---

async def schedule_reminder(
    mention_or_user: str,
    delay_minutes: int,
    reminder_text: Optional[str],
    user_id: str, # Added
    memory: MemoryInterface, # Added
    adapter: BotFrameworkAdapter, # Added
    conversation_ref: ConversationReference, # Added
    **kwargs # Catch extra args
) -> Dict[str, Any]:
    """
    Schedules a simple reminder message for the user after a delay.

    Uses the provided adapter and conversation reference to send a proactive message
    after the specified delay.

    Args:
        mention_or_user: The user/topic to mention in the reminder.
        delay_minutes: Delay before sending the reminder.
        reminder_text: Optional specific text for the reminder.
        user_id: ID of the user requesting the reminder.
        memory: Memory interface (unused in current implementation).
        adapter: BotFrameworkAdapter instance for sending the proactive message.
        conversation_ref: ConversationReference for the target chat.

    Returns:
        Dict: Success or error dictionary.
    """
    logger.info(f"Scheduling reminder for {user_id} in {delay_minutes} min: '{mention_or_user}'")
    # (Implementation remains the same as previous version)
    # ...
    if not adapter or not conversation_ref:
         # Cannot schedule without context for proactive message
         return {"error": "Cannot schedule reminder.", "suggestion": "Bot context (adapter/conversation reference) is missing. This might be an internal error."}

    if delay_minutes <= 0: return {"error": "Invalid delay.", "suggestion": "Delay must be positive."}

    logger.info(f"Executing tool: schedule_reminder for user {user_id} about '{mention_or_user}' in {delay_minutes} mins.")

    try:
        run_time = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
        job_id = f"reminder_{user_id}_{run_time.strftime('%Y%m%d%H%M%S')}"

        # Ensure _send_proactive_reminder is defined (should be in bot_core or here)
        from bot_core import _send_proactive_reminder # Assuming it's defined there now for reuse

        job = await schedule_task(
            _send_proactive_reminder, # Reference the actual function
            job_id=job_id,
            trigger_type='date',
            run_date=run_time,
            replace_existing=True,
            kwargs={
                "adapter": adapter, "conversation_ref": conversation_ref,
                "message_text": reminder_text or "You asked me to remind you about this.", "target_info": mention_or_user
            }
        )
        if job:
            return {"success": True, "message": f"OK. I will remind you about '{mention_or_user}' in {delay_minutes} minutes.", "scheduled_time_utc": run_time.isoformat()}
        else:
             return {"error": "Failed to schedule reminder.", "suggestion": "Could not add task (maybe time was in past?)."}
    except Exception as e:
        logger.exception(f"Failed to schedule reminder for user {user_id}: {e}")
        return {"error": "Failed to schedule reminder.", "suggestion": f"Unexpected error: {e}"}


async def record_demo_schedule(
    schedule_details: str,
    user_id: str, # Added
    memory: MemoryInterface, # Added
    **kwargs
) -> Dict[str, Any]:
    """
    (Mock) Records the user's request to schedule a demo.

    Currently logs the request and stores the details in user memory.

    Args:
        schedule_details: Text describing the requested demo schedule.
        user_id: ID of the user making the request.
        memory: Memory interface for storing the request details.

    Returns:
        Dict: Success confirmation dictionary.
    """
    logger.info(f"Recording demo schedule request for user {user_id}: {schedule_details}")
    # (Implementation remains the same as previous version)
    # ...
    details = schedule_details
    if not details: return {"error": "Missing demo schedule details."}
    logger.info(f"Executing tool: record_demo_schedule for user {user_id} with details: '{details}'")
    timestamp = datetime.now(timezone.utc).isoformat()
    await memory.set_user_data(user_id, f"demo_request_{timestamp}", details)
    await memory.set_user_data(user_id, "last_demo_request_details", details)
    return {"success": True, "message": "Your request to schedule a demo has been recorded.", "details_recorded": details, "next_steps": "Please finalize scheduling via calendar."}


async def suggest_next_actions(
    context_hint: Optional[str],
    user_id: str, # Added
    memory: MemoryInterface, # Added
    **kwargs
) -> Dict[str, Any]:
    """
    Provides suggested commands/actions to the user.

    Generates suggestions based on general capabilities and potentially the context hint.

    Args:
        context_hint: Optional hint about the user's current goal.
        user_id: ID of the user requesting suggestions.
        memory: Memory interface (unused in current implementation).

    Returns:
        Dict: Success dictionary containing a list of suggestions.
    """
    gemini = get_gemini_client()
    if not gemini: return {"error": "AI client not configured."}

    logger.info(f"Generating suggestions for user {user_id} (Hint: {context_hint})")
    user_data = await memory.get_user_data(user_id)

    # Format memory for prompt
    memory_context = ""
    relevant_keys = ["last_github_repo", "last_pr_details", "last_jira_project", "last_cherry_pick_issue", "last_octopus_project", "last_octopus_environment"]
    context_lines = [f"- {k.replace('_', ' ').title()}: {str(v)[:100]}" for k, v in user_data.items() if k in relevant_keys and v]
    memory_context = "\n".join(context_lines) if context_lines else "No specific context found."

    # Get simplified list of tool descriptions
    tool_descriptions = ""
    from .schemas import GEMINI_USABLE_TOOLS_LIST
    for tool in GEMINI_USABLE_TOOLS_LIST:
        tool_descriptions += f"- {tool['name']}: {tool['description']}\n"

    prompt = (
        "You are an AI assistant helping a user in a ChatOps bot.\n"
        "Based *only* on the user's recent context below and the available tools, suggest 2-3 relevant and actionable commands the user might want to run next.\n"
        "Format the suggestions as natural language commands the user could type (e.g., 'Summarize PR 123 in repo X', 'Deploy project Y to prod').\n"
        "Do NOT suggest actions if uncertain or context is too generic. Do NOT suggest actions unrelated to the tools.\n\n"
        f"Available Tools:\n{tool_descriptions}\n"
        f"User Context:\n{memory_context}\n\n"
        "Suggested next commands (provide 2-3 max, each on a new line, starting with '- '):"
    )
    try:
        response = await gemini.generate_content_async(prompt, generation_config={"temperature": 0.5}) # Slightly creative suggestions
        if not response.text: return {"success": True, "message": "I couldn't think of any specific suggestions right now."}

        suggestions = [line[2:].strip() for line in response.text.strip().split('\n') if line.strip().startswith("- ")]
        if not suggestions: return {"success": True, "message": "I couldn't think of any suggestions."}

        logger.info(f"Generated suggestions for user {user_id}: {suggestions[:3]}")
        return {"success": True, "suggestions": suggestions[:3]} # Return max 3
    except Exception as e:
        logger.exception(f"Error generating suggestions for user {user_id}: {e}")
        return {"error": "Failed to generate suggestions.", "suggestion": str(e)}


async def _update_digest_schedule(
    user_id: str, 
    enabled: bool, 
    memory: MemoryInterface, 
    adapter: BotFrameworkAdapter, 
    conv_ref: ConversationReference
):
    """Internal helper to add or remove the daily digest job for a user."""
    job_id = f"daily_digest_{user_id}"
    if not enabled:
        try: await remove_job(job_id); logger.info(f"Removed daily digest job {job_id}")
        except JobLookupError: logger.info(f"Daily digest job {job_id} not found to remove.")
        except Exception as e: logger.error(f"Error removing digest job {job_id}: {e}")
        await memory.delete_user_data(user_id, "digest_job_id") # Clear stored ID regardless
        await memory.delete_user_data(user_id, "proactive_conversation_ref") # Clear stored ref
        return

    # --- Schedule the job ---
    if not adapter or not conv_ref:
        logger.error(f"Cannot schedule digest for user {user_id}: Adapter or ConversationRef missing.")
        await memory.set_user_data(user_id, "digest_pending_schedule", True) # Mark as pending
        return

    user_tz_str = await memory.get_user_value(user_id, "digest_timezone", settings.DEFAULT_USER_TIMEZONE)
    try: user_tz = pytz.timezone(user_tz_str)
    except pytz.UnknownTimeZoneError:
        logger.error(f"Invalid timezone '{user_tz_str}' for user {user_id}, using default {settings.DEFAULT_USER_TIMEZONE}.")
        user_tz = pytz.timezone(settings.DEFAULT_USER_TIMEZONE)

    # Store conversation reference persistently (CRITICAL: handle potential expiration)
    await memory.set_user_data(user_id, "proactive_conversation_ref", conv_ref.as_dict()) # Store as dict

    try:
        job = await schedule_task(
            send_user_digest,
            job_id=job_id,
            trigger_type='cron',
            hour=8, minute=0, # 8:00 AM
            timezone=user_tz,
            replace_existing=True,
            args=[user_id, memory, adapter, conv_ref], # Pass necessary args
            misfire_grace_time=3600
        )
        if job:
            await memory.set_user_data(user_id, "digest_job_id", job.id)
            await memory.delete_user_data(user_id, "digest_pending_schedule") # Clear pending flag
            logger.info(f"Scheduled daily digest job {job_id} for user {user_id} at 8:00 AM {user_tz_str}.")
        else:
             logger.error(f"Failed to schedule digest job {job_id} (schedule_task returned None).")
    except Exception as e:
        logger.exception(f"Failed to schedule daily digest for user {user_id}: {e}")


async def manage_preferences(
    action: Literal['set', 'get', 'list', 'digest'],
    pref_name: Optional[str],
    pref_value: Optional[str],
    user_id: str,
    memory: MemoryInterface,
    adapter: Optional[BotFrameworkAdapter] = None, # Optional for digest scheduling
    conversation_ref: Optional[ConversationReference] = None, # Optional for digest scheduling
    **kwargs
) -> Dict[str, Any]:
    """
    Manages user preferences (get, set, list) and daily digest settings.

    Handles actions:
    - 'list': Retrieves and returns all user preferences.
    - 'get': Retrieves a specific preference value.
    - 'set': Sets or updates a preference value.
    - 'digest': Enables/disables the daily digest and schedules/unschedules the job.
               Requires adapter and conversation_ref if enabling.

    Args:
        action: The preference action to perform ('set', 'get', 'list', 'digest').
        pref_name: The name of the preference (for 'set', 'get').
        pref_value: The value to set (for 'set', 'on'/'off' for 'digest').
        user_id: The ID of the user whose preferences are being managed.
        memory: The memory interface for storing/retrieving preferences.
        adapter: BotFrameworkAdapter, required if action is 'digest' and enabling.
        conversation_ref: ConversationReference, required if action is 'digest' and enabling.

    Returns:
        Dict: Success or error dictionary, potentially containing preference data.
    """
    logger.info(f"Managing preferences for user {user_id}: Action={action}, Pref={pref_name}")
    # (Implementation remains the same as previous version)
    # ...
    if action == "list":
        user_prefs = await memory.get_user_data(user_id)
        prefs_to_show = {k: v for k, v in user_prefs.items() if k in ALLOWED_PREFS or k == "command_aliases"}
        if not prefs_to_show: return {"success": True, "message": "You haven't set any preferences yet."}
        # Return structured data for card
        return {"success": True, "message": "Your current preferences:", "preferences": prefs_to_show}

    elif action == "get":
        if not pref_name or pref_name not in ALLOWED_PREFS: return {"error": f"Invalid preference name.", "suggestion": f"Allowed keys: {', '.join(ALLOWED_PREFS.keys())}"}
        value = await memory.get_user_value(user_id, pref_name)
        if value is None: return {"success": True, "message": f"Preference '{pref_name}' is not set."}
        else: return {"success": True, "message": f"Preference '{pref_name}' is: `{value}`"}

    elif action == "set":
        if not pref_name or pref_name not in ALLOWED_PREFS: return {"error": f"Invalid preference name.", "suggestion": f"Allowed keys: {', '.join(ALLOWED_PREFS.keys())}"}
        if pref_value is None: return {"error": f"No value provided for preference '{pref_name}'."}

        target_type = ALLOWED_PREFS[pref_name]["type"]
        final_value: Any = pref_value
        try:
            if target_type == bool: final_value = pref_value.lower() in ['true', 'yes', '1', 'on']
            elif target_type == int: final_value = int(pref_value)
        except ValueError: return {"error": f"Invalid value format for '{pref_name}'. Expected type: {target_type.__name__}."}

        # Specific validation
        if pref_name == "default_repo" and not re.match(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+$", final_value): return {"error": "Invalid repository format.", "suggestion": "Use 'owner/repo'."}
        if pref_name == "digest_timezone":
            try: pytz.timezone(final_value)
            except pytz.UnknownTimeZoneError: return {"error": f"Invalid timezone '{final_value}'.", "suggestion": "Use a valid TZ database name."}

        await memory.set_user_data(user_id, pref_name, final_value)
        logger.info(f"Set preference '{pref_name}' to '{final_value}' for user {user_id}")

        # Handle side effects (scheduling digest)
        if pref_name == "digest_enabled":
             await _update_digest_schedule(user_id, final_value, memory, adapter, conversation_ref)
        elif pref_name == "digest_timezone" and await memory.get_user_value(user_id, "digest_enabled", False):
             # Reschedule if timezone changes while enabled
             await _update_digest_schedule(user_id, True, memory, adapter, conversation_ref)

        return {"success": True, "message": f"Preference '{pref_name}' set to `{final_value}`."}

    elif action == "digest": # Specific toggle action
        if pref_value is None or pref_value.lower() not in ['on', 'off']: return {"error": "Invalid digest value.", "suggestion": "Use '/pref digest on' or '/pref digest off'."}
        enabled = pref_value.lower() == 'on'
        await memory.set_user_data(user_id, "digest_enabled", enabled)
        await _update_digest_schedule(user_id, enabled, memory, adapter, conversation_ref) # Update schedule
        status_msg = "enabled and scheduled" if enabled else "disabled"
        return {"success": True, "message": f"Daily digest has been {status_msg}."}

    else: return {"error": f"Unknown preference action: {action}"}


async def manage_aliases(
    action: Literal['set', 'delete', 'list'],
    alias_name: Optional[str],
    command_string: Optional[str],
    user_id: str,
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Manages user-defined command aliases.

    Handles actions:
    - 'list': Retrieves and returns all user aliases.
    - 'set': Creates or updates an alias.
    - 'delete': Removes an alias.

    Args:
        action: The alias action to perform ('set', 'delete', 'list').
        alias_name: The name for the alias (single word, for 'set', 'delete').
        command_string: The command the alias expands to (for 'set').
        user_id: The ID of the user whose aliases are being managed.
        memory: The memory interface for storing/retrieving aliases.

    Returns:
        Dict: Success or error dictionary, potentially containing alias data.
    """
    logger.info(f"Managing aliases for user {user_id}: Action={action}, Alias={alias_name}")
    # (Implementation remains the same as previous version)
    # ...
    if action == "list":
        aliases: Dict[str, str] = await memory.get_user_value(user_id, "command_aliases", {})
        if not aliases: return {"success": True, "message": "You have no command aliases defined."}
        else: return {"success": True, "message": "Your current command aliases:", "aliases": aliases} # Return dict for card

    if not alias_name: return {"error": "Alias name required for 'set'/'delete'."}
    if not re.match(r"^[a-zA-Z0-9_-]+$", alias_name): return {"error": "Invalid alias name.", "suggestion": "Use letters, numbers, _, -."}
    if alias_name.lower() in ["alias", "pref", "suggest", "explain"]: return {"error": f"Alias name '{alias_name}' is reserved."}

    if action == "delete":
        aliases: Dict[str, str] = await memory.get_user_value(user_id, "command_aliases", {})
        if alias_name in aliases:
            del aliases[alias_name]
            await memory.set_user_data(user_id, "command_aliases", aliases)
            logger.info(f"Deleted alias '{alias_name}' for user {user_id}")
            return {"success": True, "message": f"Alias `/{alias_name}` deleted."}
        else: return {"error": f"Alias `/{alias_name}` not found."}
    elif action == "set":
        if not command_string: return {"error": "Command string required for 'set'."}
        if command_string.strip().startswith(f"/{alias_name}"): return {"error": "Alias cannot call itself."}
        aliases: Dict[str, str] = await memory.get_user_value(user_id, "command_aliases", {})
        aliases[alias_name] = command_string.strip()
        await memory.set_user_data(user_id, "command_aliases", aliases)
        logger.info(f"Set alias '{alias_name}' to '{command_string}' for user {user_id}")
        return {"success": True, "message": f"Alias `/{alias_name}` set to `{command_string}`."}
    else: return {"error": f"Unknown alias action: {action}"}


async def explain_item(
    item_identifier: str,
    user_id: str,
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Provides an AI-powered explanation of a GitHub PR or Jira issue.

    Parses the identifier to determine type (PR/Jira) and details.
    Fetches relevant data using internal tools (get_pr_summary, get_jira_issue_details).
    Sends the data to Gemini for explanation.

    Args:
        item_identifier: String identifying the item (e.g., 'PR 123', 'owner/repo#45', 'JIRA-123').
        user_id: ID of the user requesting the explanation.
        memory: Memory interface, used to potentially resolve default repo/project.

    Returns:
        Dict: Success dictionary with the explanation or error dictionary.
    """
    gemini = get_gemini_client()
    if not gemini: return {"error": "AI client not configured."}

    identifier = item_identifier
    logger.info(f"Attempting to explain item '{identifier}' for user {user_id}")

    item_type = None; item_key = None; repo_context = None

    # --- Identify Item --- (Regex from previous version)
    pr_match = re.match(r"(?:pr|pull)?\s*#?(\d+)(?:\s+in\s+([a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+))?", identifier, re.IGNORECASE)
    repo_pr_match = re.match(r"([a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+)#(\d+)", identifier, re.IGNORECASE)
    jira_match = re.match(r"(?:jira|issue|ticket)?\s*([A-Z][A-Z0-9]+-\d+)", identifier, re.IGNORECASE)

    if repo_pr_match: item_type, repo_context, item_key = "pr", repo_pr_match.group(1), repo_pr_match.group(2)
    elif pr_match: item_type, item_key, repo_context = "pr", pr_match.group(1), pr_match.group(2)
    elif jira_match: item_type, item_key = "jira", jira_match.group(1).upper()
    else: return {"error": "Could not identify item (PR or Jira issue).", "suggestion": "Use formats like 'PR 123', 'JIRA-456', 'owner/repo#123'."}

    # --- Fetch Item Details ---
    item_details = None; fetch_error = None; fetch_suggestion = None
    if item_type == "pr":
        if not item_key.isdigit(): return {"error": "Invalid PR number."}
        pr_num = int(item_key)
        repo_name = repo_context or await memory.get_user_value(user_id, "last_github_repo") or settings.DEFAULT_GITHUB_REPO
        if not repo_name: return {"error": "GitHub repository context missing."}
        details_result = await execute_tool("github_pr_summary", {"repo_name": repo_name, "pr_number": pr_num}, memory, user_id, "Explain Bot")
        if details_result.get("success"): item_details = details_result
        else: fetch_error, fetch_suggestion = details_result.get("error"), details_result.get("suggestion")
    elif item_type == "jira":
        details_result = await execute_tool("get_jira_issue_details", {"issue_key": item_key}, memory, user_id, "Explain Bot")
        if details_result.get("success"): item_details = details_result.get("issue_details")
        else: fetch_error, fetch_suggestion = details_result.get("error"), details_result.get("suggestion")

    if fetch_error: return {"error": fetch_error, "suggestion": fetch_suggestion}
    if not item_details: return {"error": "Could not retrieve item details."}

    # --- Generate Explanation ---
    details_for_prompt = ""
    try: # Limit context sent to LLM
         details_str = json.dumps(item_details, default=str) # Handle datetime etc.
         details_for_prompt = (details_str[:15000] + '... (truncated)') if len(details_str) > 15000 else details_str
    except Exception as json_err:
         logger.error(f"Error serializing item details for explain prompt: {json_err}")
         details_for_prompt = f"Error serializing details. Key: {item_key}, Title: {item_details.get('summary', 'N/A')}"

    prompt = (
         f"You are an AI assistant explaining a {item_type.upper()} item to a developer.\n"
         f"Based ONLY on the following JSON data, provide a concise explanation (3-5 sentences, use markdown).\n"
         f"Focus on: Purpose/Goal? Key changes/activity? Current status? Any risks or blockers mentioned in description/comments?\n"
         f"Do NOT just list the data fields. Synthesize an explanation.\n\n"
         f"Item Data ({item_type.upper()} {item_key}):\n```json\n{details_for_prompt}\n```\n\n"
         f"Explanation:"
     )
    try:
        response = await gemini.generate_content_async(prompt, generation_config={"temperature": 0.4}) # More factual explanation
        explanation = response.text.strip() if response.text else "Could not generate an explanation."
        logger.info(f"Generated explanation for {item_type} {item_key}")
        return {"success": True, "item_type": item_type, "item_key": item_key, "item_details": item_details, "explanation": explanation}
    except Exception as e:
        logger.exception(f"Error generating explanation for {item_type} {item_key}: {e}")
        return {"success": True, "item_type": item_type, "item_key": item_key, "item_details": item_details, "explanation": f"AI explanation failed: {e}", "warning": "Explanation generation failed."} 