from typing import Dict, Any
import dateutil.parser # Not used here currently, but potentially useful

def create_octopus_notification_card(event_data: Dict[str, Any]) -> dict | None:
    """Generates card for Octopus Deploy webhook events."""
    payload = event_data.get("Payload", {})
    event_category = payload.get("EventCategory")
    message = payload.get("Message", "Octopus Event"); server_uri = payload.get("ServerUri", "")
    space_name = payload.get("SpaceName", "N/A")

    if event_category in ["DeploymentSucceeded", "DeploymentFailed", "DeploymentStarted"]:
        event_details = payload.get("Event", {})
        related_docs = event_details.get("RelatedDocumentIds", [])
        project_name = event_details.get("ProjectName", "N/A"); env_name = event_details.get("EnvironmentName", "N/A")
        release_version = event_details.get("ReleaseVersion", "N/A"); user = payload.get("Username", "N/A")

        status = "Unknown"; status_color = "default"; icon = "❓"
        if event_category == "DeploymentSucceeded": status, status_color, icon = "Succeeded", "good", "✅"
        elif event_category == "DeploymentFailed": status, status_color, icon = "Failed", "attention", "❌"
        elif event_category == "DeploymentStarted": status, status_color, icon = "Started", "accent", "🚀"

        title = f"🐙 Octopus: {project_name} to {env_name} {status}"
        task_link = None
        task_id = next((doc_id for doc_id in related_docs if doc_id.startswith("ServerTasks-")), None)
        if task_id and server_uri and payload.get("SpaceId"): task_link = f"{server_uri}/app#/{payload['SpaceId']}/tasks/{task_id}"

        card = {
            "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
            "body": [
                {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": title, "color": status_color},
                {"type": "TextBlock", "text": message, "wrap": True, "spacing": "Small"},
                {"type": "FactSet", "spacing": "Medium", "facts": [
                    {"title": "Project", "value": project_name}, {"title": "Environment", "value": env_name},
                    {"title": "Version", "value": release_version}, {"title": "Triggered By", "value": user},
                    {"title": "Space", "value": space_name},
                ]}
            ], "actions": []
        }
        if task_link: card["actions"].append({"type": "Action.OpenUrl", "title": "View Task", "url": task_link})
        return card

    # logger.warning(f"No card handler for Octopus webhook event category: {event_category}")
    return None 