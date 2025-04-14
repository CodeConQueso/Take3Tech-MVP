from typing import Dict, Any

def create_confirmation_card(title: str, message: str, details: dict[str, Any] = None) -> dict:
    """Generates a generic confirmation Adaptive Card."""
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock", "size": "Medium", "weight": "Bolder",
                "text": f"✅ {title}", "color": "good"
            },
            {
                "type": "TextBlock", "text": message, "wrap": True, "spacing": "Small"
            }
        ]
    }
    if details:
        # Filter out None/empty values and format nicely
        facts = [{"title": str(k).replace('_', ' ').title(), "value": str(v)} for k, v in details.items() if v is not None and str(v).strip() != '']
        if facts:
            card["body"].append({"type": "FactSet", "facts": facts, "spacing": "Medium"})
    return card 