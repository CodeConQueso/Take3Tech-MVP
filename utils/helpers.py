import re
import logging
import hashlib
import hmac
import asyncio # Added for timeout/sleep in retries
from typing import Dict, Any, Optional
from botbuilder.schema import Activity, ActivityTypes, CardAction, ActionTypes, Attachment, Mention, ConversationReference
from botbuilder.core import TurnContext, CardFactory, MessageFactory, BotFrameworkAdapter
from botbuilder.core.bot_framework_adapter import BotFrameworkAdapter # Explicit import for type hint
from botbuilder.core.bot_assert import BotAssert # For type checking
from botbuilder.schema._models_py3 import ConversationReference # Explicit import
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from utils.config import settings

logger = logging.getLogger(__name__)

def clean_message_text(activity: Activity) -> str:
    """Removes bot mentions and leading/trailing whitespace from message text."""
    text = activity.text or "" # Ensure text is not None
    if activity.recipient:
        mention_pattern = r"<at>" + re.escape(activity.recipient.name) + r"</at>\s*"
        text = re.sub(mention_pattern, "", text).strip()

    if activity.entities:
        mentions = [entity for entity in activity.entities if entity.type == "mention"]
        for entity in mentions:
            if entity.mentioned and entity.mentioned.id == activity.recipient.id:
                if entity.text and entity.text in text:
                    text = text.replace(entity.text, "").strip()

    return text.strip()


def create_adaptive_card_attachment(card_json: Dict[str, Any]) -> Attachment:
    """Creates an Attachment object from Adaptive Card JSON."""
    return Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card_json
    )

async def send_adaptive_card(turn_context: TurnContext, card_json: Dict[str, Any]) -> Optional[Activity]:
    """Sends a message activity with the given Adaptive Card JSON and returns the sent activity."""
    if not card_json:
        logger.error("Attempted to send an empty card.")
        return None
    attachment = create_adaptive_card_attachment(card_json)
    activity = Activity(type=ActivityTypes.message, attachments=[attachment])
    try:
        response = await turn_context.send_activity(activity)
        # Access the result from the ResourceResponse to get the sent Activity object
        # This depends on the BotBuilder SDK version; adjust if needed.
        # Assuming send_activity returns ResourceResponse containing activity id.
        # To return the full Activity, we might need to reconstruct it or rely on the caller context.
        # Let's return the ID for now, or the reconstructed activity if possible.
        if response and response.id:
            activity.id = response.id
            return activity # Return the activity object with its ID
        return None
    except Exception as e:
        logger.error(f"Error sending adaptive card: {e}", exc_info=True)
        # Send plain text fallback
        await send_message(turn_context, "Sorry, there was an error displaying the card content.")
        return None


async def send_message(turn_context: TurnContext, text: str) -> Activity | None:
    """Sends a simple text message and returns the sent activity."""
    activity = MessageFactory.text(text)
    try:
        resource_response = await turn_context.send_activity(activity)
        if resource_response and resource_response.id:
            activity.id = resource_response.id
            return activity
        return None
    except Exception as e:
        logger.error(f"Error sending text message: {e}", exc_info=True)
        return None


async def send_error_message(turn_context: TurnContext, error_message: str, suggestion: str = None):
    """Sends a standardized error message using an Adaptive Card."""
    # Avoid circular import at module level
    from cards.error_card import create_error_card
    error_card = create_error_card(error_message, suggestion)
    await send_adaptive_card(turn_context, error_card)
    logger.warning(f"Sent error message to user {get_user_id(turn_context.activity)}: {error_message}")


def get_user_id(activity: Activity) -> str:
    """Extracts the unique user ID (AAD Object ID preferably) from the activity."""
    # Prefer AAD Object ID for stable identification across Teams
    aad_object_id = activity.from_property.aad_object_id
    user_id = activity.from_property.id # Fallback to Teams internal ID (29:xxx)

    if aad_object_id:
        return aad_object_id
    elif user_id:
        # Log if we have to fall back, as AAD ID is better for Graph/RBAC
        logger.debug(f"Using fallback user ID {user_id} as AAD Object ID is missing.")
        return user_id
    else:
        logger.error("Could not extract any user ID from activity.")
        raise ValueError("Cannot identify user for operations.")


def get_conversation_reference(activity: Activity) -> Optional[ConversationReference]:
    """
    Gets the conversation reference for proactive messaging.
    Returns None if reference cannot be obtained.
    """
    try:
        # TurnContext.get_conversation_reference is a static method
        conversation_reference = TurnContext.get_conversation_reference(activity)
        if not conversation_reference:
             logger.error("TurnContext.get_conversation_reference returned None.")
             return None
        # Ensure essential fields are present
        if not conversation_reference.conversation or not conversation_reference.conversation.id:
             logger.error("Conversation reference is missing conversation ID.")
             return None
        if not conversation_reference.service_url:
             logger.error("Conversation reference is missing service URL.")
             return None
        return conversation_reference
    except Exception as e:
        logger.error(f"Error getting conversation reference: {e}", exc_info=True)
        return None


def get_mentioned_user_ids(activity: Activity) -> list[str]:
    """Finds AAD Object IDs of all mentioned users (excluding the bot)."""
    mentioned_ids = []
    if activity.entities:
        for entity in activity.entities:
            if entity.type == "mention" and hasattr(entity, 'mentioned') and entity.mentioned:
                 if entity.mentioned.id != activity.recipient.id:
                     # Prefer AAD Object ID
                     user_id = entity.mentioned.aad_object_id or entity.mentioned.id
                     if user_id: mentioned_ids.append(user_id)
    return list(set(mentioned_ids))


# --- Webhook Security Helpers ---

def verify_github_signature(payload_body: bytes, signature_header: Optional[str], secret: str) -> bool:
    """Verifies the GitHub webhook signature using the provided secret."""
    if not signature_header:
        logger.warning("Missing X-Hub-Signature-256 header for GitHub webhook.")
        return False
    if not secret:
        logger.error("Webhook secret is not configured. Cannot verify GitHub webhook signature.")
        return False

    hash_object = hmac.new(secret.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()

    if not hmac.compare_digest(expected_signature, signature_header):
        logger.warning(f"GitHub webhook signature mismatch. Expected: {expected_signature}, Got: {signature_header}")
        return False
    
    logger.debug("GitHub webhook signature verified successfully.")
    return True

# TODO: Implement verify_octopus_signature if needed (depends on Octopus config)
def verify_octopus_signature(payload_body: bytes, signature_header: Optional[str], secret: Optional[str]) -> bool:
    """Placeholder for verifying Octopus webhook signature."""
    if not secret:
        logger.info("OCTOPUS_WEBHOOK_SECRET not configured. Skipping Octopus signature verification.")
        return True
    
    # --- Add Octopus Verification Logic Here --- 
    # Example (if using HMAC SHA1 similar to GitHub but SHA1):
    # if not signature_header or not signature_header.startswith("sha1="):
    #     logger.warning("Missing or invalid X-Octopus-Signature header.")
    #     return False
    # hash_object = hmac.new(settings.OCTOPUS_WEBHOOK_SECRET.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha1)
    # expected_signature = "sha1=" + hash_object.hexdigest()
    # if not hmac.compare_digest(expected_signature, signature_header):
    #     logger.warning("Octopus webhook signature mismatch.")
    #     return False
    # logger.debug("Octopus webhook signature verified.")
    # return True
    # -------------------------------------------

    logger.warning("Octopus signature verification is not fully implemented.")
    # For now, return True but log a warning. Implement based on actual setup.
    return True

# --- Proactive Messaging Helper ---

# Define exceptions from BotBuilder SDK that indicate transient errors
# Note: This might need adjustment based on observed errors
RETRYABLE_BOT_FRAMEWORK_ERRORS = (
    # Add specific exceptions if known, e.g., related to timeouts or temporary service unavailability
    # For now, retry on generic Exception, but be cautious
    Exception 
)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(RETRYABLE_BOT_FRAMEWORK_ERRORS),
    reraise=True
)
async def send_proactive_message(
    adapter: BotFrameworkAdapter, 
    conversation_reference: ConversationReference, 
    message: str | None = None, 
    attachments: list[Attachment] | None = None,
    description: str = "Proactive Message" # Added for logging clarity
    ):
    """Sends a proactive message (text and/or attachments) to a stored conversation reference, with retries."""
    
    # --- Input Validation ---
    BotAssert.adapter_is_not_none(adapter)
    BotAssert.reference_is_not_none(conversation_reference)
    if not message and not attachments:
        logger.error("Attempted to send proactive message with no content (message or attachments).")
        return
    if not conversation_reference.conversation or not conversation_reference.conversation.id:
        logger.error("Cannot send proactive message: ConversationReference is missing conversation ID.")
        return
    if not conversation_reference.service_url:
        logger.error("Cannot send proactive message: ConversationReference is missing service URL.")
        return

    logger.info(f"Attempting to send {description} to conv {conversation_reference.conversation.id} (Attempt info via retry log)...")

    async def proactive_callback(proactive_turn_context: TurnContext):
        # Create the activity to send inside the callback
        activity_to_send = Activity(type=ActivityTypes.message)
        if message:
            activity_to_send.text = message
        if attachments:
            activity_to_send.attachments = attachments
        
        try:
            await proactive_turn_context.send_activity(activity_to_send)
            logger.info(f"Successfully sent {description} to conv {conversation_reference.conversation.id}")
        except Exception as send_error:
            # Errors inside the callback might not be caught by the main retry decorator
            # Depending on BotBuilder behavior. Log them critically.
            logger.error(f"Failed to send activity within proactive callback for {description} to {conversation_reference.conversation.id}: {send_error}", exc_info=True)
            # Don't try fallback here as it might loop infinitely if the issue is fundamental
            raise # Reraise to potentially fail the continue_conversation call
    
    try:
        # The actual call to the Bot Framework SDK
        await adapter.continue_conversation(
            conversation_reference, 
            proactive_callback, 
            app_id=settings.MICROSOFT_APP_ID
        )
    except RETRYABLE_BOT_FRAMEWORK_ERRORS as e:
        # Log the warning here, tenacity will handle the retry
        logger.warning(f"Error sending proactive message for {description} (conv: {conversation_reference.conversation.id}): {e}. Retrying...")
        raise # Reraise for tenacity
    except Exception as e:
        # Catch non-retryable errors after potential retries
        logger.error(f"Failed to send proactive message for {description} (conv: {conversation_reference.conversation.id}) after retries or due to non-retryable error: {e}", exc_info=True)
        # Optionally, implement a dead-letter queue or other handling for persistent failures here. 