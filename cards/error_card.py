from typing import Optional

def create_error_card(error_message: str, suggestion: Optional[str] = None) -> dict:
    """Generates a standardized error Adaptive Card."""
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock", "size": "Medium", "weight": "Bolder",
                "text": "⚠️ Oops! Something went wrong.", "color": "attention"
            },
            {
                "type": "TextBlock", "text": error_message, "wrap": True, "spacing": "Medium"
            }
        ]
    }
    if suggestion:
        card["body"].append({
            "type": "TextBlock", "text": f"Suggestion: {suggestion}",
            "wrap": True, "isSubtle": True, "spacing": "Small"
        })
    return card 