import logging
from fastapi import BackgroundTasks, HTTPException
from botbuilder.core import BotFrameworkAdapter
from botbuilder.schema import ConversationReference

from memory.interface import MemoryInterface
from utils.config import Settings
from utils.helpers import send_proactive_message
from cards import (
    create_github_pr_notification_card,
    create_octopus_deployment_notification_card,
)

logger = logging.getLogger(__name__)


async def process_github_event(
    adapter: BotFrameworkAdapter,
    background_tasks: BackgroundTasks,
    current_memory: MemoryInterface,
    settings: Settings,
    event_type: str,
    payload: dict,
    repo_name: str,
):
    """
    Processes a validated GitHub webhook event received via the API endpoint.

    Looks up the target Teams channel ID based on the repository name using
    `settings.GITHUB_REPO_CHANNEL_MAP`.
    Retrieves the corresponding conversation reference from memory.
    Generates an appropriate Adaptive Card based on the event type (e.g., pull request)
    using functions from the `cards` module.
    Schedules a background task (`send_proactive_message`) to send the card to the target channel.

    Args:
        adapter: The Bot Framework Adapter instance.
        background_tasks: FastAPI BackgroundTasks instance for scheduling the proactive message.
        current_memory: The memory interface instance to fetch conversation references.
        settings: The application settings instance containing channel mappings.
        event_type: The type of GitHub event (e.g., 'pull_request').
        payload: The parsed JSON payload of the webhook.
        repo_name: The full name of the repository the event originated from (e.g., 'owner/repo').
    """
    logger.info(f"Processing GitHub event '{event_type}' for repo '{repo_name}'")

    target_channel_id = settings.GITHUB_REPO_CHANNEL_MAP.get(repo_name)
    if not target_channel_id:
        logger.warning(
            f"No channel mapping found for GitHub repository: {repo_name}"
        )
        return  # Or raise HTTPException(status_code=404, detail="Repo mapping not found")

    conv_ref: ConversationReference | None = (
        await current_memory.get_conversation_reference(target_channel_id)
    )
    if not conv_ref:
        logger.error(
            f"Conversation reference not found for channel ID: {target_channel_id} (GitHub Repo: {repo_name})"
        )
        # Depending on requirements, could raise an error or just log and return
        return

    try:
        # --- Card Generation Logic ---
        # Adapt this based on the specific event_type and payload structure
        card = None
        if event_type == "pull_request" and payload.get("action") in ["opened", "reopened", "closed", "merged"]:
            # Assuming merged status is within the payload if action is 'closed'
            # You might need more specific parsing based on actual GitHub payloads
            card = create_github_pr_notification_card(payload)
        # Add handlers for other event types (push, issues, etc.) as needed
        # elif event_type == "push":
        #     card = create_github_push_notification_card(payload) # Example

        if card:
            logger.debug(f"Scheduling proactive message for GitHub event to channel {target_channel_id}")
            background_tasks.add_task(
                send_proactive_message,
                adapter=adapter,
                conversation_reference=conv_ref,
                message="Received a GitHub notification.", # Fallback text
                attachments=[card],
            )
        else:
             logger.info(f"No card generated for GitHub event type '{event_type}' and action '{payload.get('action')}' in repo '{repo_name}'.")

    except Exception as e:
        logger.exception(
            f"Error processing GitHub event for repo {repo_name}: {e}",
            exc_info=True,
        )
        # Optionally re-raise or handle differently


async def process_octopus_event(
    adapter: BotFrameworkAdapter,
    background_tasks: BackgroundTasks,
    current_memory: MemoryInterface,
    settings: Settings,
    event_category: str,
    payload: dict,
    project_id: str, # Assuming project ID is available or derivable
):
    """
    Processes a validated Octopus Deploy webhook event received via the API endpoint.

    Looks up the target Teams channel ID based on the project ID using
    `settings.OCTOPUS_PROJECT_CHANNEL_MAP`.
    Retrieves the corresponding conversation reference from memory.
    Generates an appropriate Adaptive Card based on the event category (e.g., DeploymentSucceeded)
    using functions from the `cards` module.
    Schedules a background task (`send_proactive_message`) to send the card to the target channel.

    Args:
        adapter: The Bot Framework Adapter instance.
        background_tasks: FastAPI BackgroundTasks instance for scheduling the proactive message.
        current_memory: The memory interface instance to fetch conversation references.
        settings: The application settings instance containing channel mappings.
        event_category: The category of the Octopus event (e.g., 'DeploymentSucceeded').
        payload: The parsed JSON payload of the webhook.
        project_id: The Octopus project ID the event relates to (e.g., 'Projects-123').
    """
    logger.info(f"Processing Octopus event '{event_category}' for project '{project_id}'")

    target_channel_id = settings.OCTOPUS_PROJECT_CHANNEL_MAP.get(project_id)
    if not target_channel_id:
        logger.warning(
            f"No channel mapping found for Octopus project ID: {project_id}"
        )
        return

    conv_ref: ConversationReference | None = (
        await current_memory.get_conversation_reference(target_channel_id)
    )
    if not conv_ref:
        logger.error(
            f"Conversation reference not found for channel ID: {target_channel_id} (Octopus Project: {project_id})"
        )
        return

    try:
        # --- Card Generation Logic ---
        # Adapt based on event_category and payload details
        card = None
        if event_category in ["DeploymentSucceeded", "DeploymentFailed"]:
             # You'll likely need to extract specific details from the payload
             # for the card function. Adjust create_octopus_deployment_notification_card accordingly.
            card = create_octopus_deployment_notification_card(payload)
        # Add handlers for other event categories as needed

        if card:
            logger.debug(f"Scheduling proactive message for Octopus event to channel {target_channel_id}")
            background_tasks.add_task(
                send_proactive_message,
                adapter=adapter,
                conversation_reference=conv_ref,
                message="Received an Octopus Deploy notification.", # Fallback text
                attachments=[card],
            )
        else:
            logger.info(f"No card generated for Octopus event category '{event_category}' in project '{project_id}'.")

    except Exception as e:
        logger.exception(
            f"Error processing Octopus event for project {project_id}: {e}",
            exc_info=True,
        )
        # Optionally re-raise or handle differently 