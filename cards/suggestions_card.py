from typing import List

def create_suggestions_card(suggestions: List[str]) -> dict:
    """Generates an Adaptive Card displaying suggested commands with clickable buttons."""
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": "Here are a few things I think I can help with:",
                "wrap": True,
                "weight": "Bolder"
            }
        ],
        "actions": []
    }

    if not suggestions:
         card["body"].append({
             "type": "TextBlock", "text": "I couldn't think of specific suggestions right now. Try asking me to do something specific!",
             "wrap": True, "isSubtle": True
         })
    else:
        # Use ActionSet for better grouping of buttons if needed, or just list actions
        for suggestion in suggestions:
            card["actions"].append({
                "type": "Action.Submit",
                "title": suggestion, # Button text is the suggestion
                "data": { # Use messageBack to simulate user typing the command
                    "msteams": {"type": "messageBack", "text": suggestion},
                    "suggestion_text": suggestion # Keep original for logging/tracking if needed
                }
            })

    return card 