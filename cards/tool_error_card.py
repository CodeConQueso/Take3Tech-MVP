from typing import Dict, Any, Optional

def create_tool_error_card(tool_name: str, error_details: Dict[str, Any], original_query: Optional[str] = None) -> dict:
    """Generates an Adaptive Card for tool execution failures."""
    error_message = error_details.get("error", "An unknown error occurred.")
    suggestion = error_details.get("suggestion", "Check logs or try again.")
    status_code = error_details.get("status_code")
    tool_display_name = tool_name.replace('_', ' ').title()

    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": f"⚠️ Error Running: {tool_display_name}", "color": "attention"},
            {"type": "TextBlock", "text": f"**Error:** {error_message}" + (f" (Status: {status_code})" if status_code else ""), "wrap": True, "spacing": "Medium"},
            {"type": "TextBlock", "text": f"**Suggestion:** {suggestion}", "wrap": True, "spacing": "Small", "isSubtle": True}
        ],
        "actions": []
    }
    if original_query:
        card["actions"].append({"type": "Action.Submit", "title": "Retry Request", "data": {"action": "retry_request", "original_query": original_query}})
    card["actions"].append({"type": "Action.Submit", "title": "Cancel", "data": {"action": "cancel_request"}})
    return card 