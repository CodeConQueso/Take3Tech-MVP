from typing import Dict, Any, List

def create_feedback_summary_card(result_data: Dict[str, Any]) -> dict:
    """Generates an Adaptive Card for the aggregated feedback summary."""
    project_key = result_data.get("project_key", "N/A")
    summary = result_data.get("summary", "No summary generated.")
    source_count = result_data.get("source_count", 0)
    issue_keys = result_data.get("issue_keys", [])

    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": f"📊 Feedback Summary for {project_key}"},
            {"type": "TextBlock", "text": f"Generated from {source_count} item(s)." if source_count > 0 else "No recent items found.", "wrap": True, "isSubtle": True},
            {"type": "TextBlock", "text": summary, "wrap": True, "spacing": "Medium"}
        ]
    }
    if issue_keys: card["body"].append({"type": "TextBlock", "text": f"Based on issues like: {', '.join(issue_keys)}...", "wrap": True, "isSubtle": True, "spacing": "Small"})
    # Optional: Add action to view project in Jira
    # from utils.config import settings
    # if project_key != "N/A" and settings.JIRA_SERVER:
    #      card["actions"] = [{"type": "Action.OpenUrl", "title": f"View {project_key} in Jira", "url": f"{settings.JIRA_SERVER}/projects/{project_key}/issues"}]
    return card 