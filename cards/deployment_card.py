from typing import Dict, Any
import dateutil.parser

def create_deployment_card(result_data: Dict[str, Any]) -> dict:
    """Generates an Adaptive Card for Octopus deployment results."""
    status_text = "Deployment Initiated Successfully"
    status_color = "good"
    message = result_data.get("message", "Deployment triggered.")
    details = {
        "Project": result_data.get("project_name"),
        "Environment": result_data.get("environment_name"),
        "Version": result_data.get("release_version"),
        "Tenant": result_data.get("tenant_name"),
        "Task ID": result_data.get("task_id"),
        "Comments": result_data.get("comments"),
    }
    filtered_details = {k: v for k, v in details.items() if v is not None}
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock", "size": "Medium", "weight": "Bolder",
                "text": f"🐙 {status_text}", "color": status_color
            },
            {"type": "TextBlock", "text": message, "wrap": True, "spacing": "Small"},
            {"type": "FactSet", "facts": [{"title": k, "value": str(v)} for k, v in filtered_details.items()], "spacing": "Medium"}
        ],
        "actions": []
    }
    task_link = result_data.get("server_task_link")
    if task_link:
        card["actions"].append({"type": "Action.OpenUrl", "title": "View Task in Octopus", "url": task_link})
    return card 