from typing import List, Dict, Any

def create_clarification_card(original_query: str, missing_params: List[str], suggestions: Dict[str, Any] = None) -> dict:
    """
    Generates an Adaptive Card to ask the user for missing parameters.
    """
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "text": "I need a bit more information for:",
                "wrap": True,
                "weight": "Bolder"
            },
            {
                "type": "TextBlock",
                "text": f"> {original_query}", # Quote the original request
                "wrap": True, "isSubtle": True, "separator": True, "spacing": "Small"
            },
            {
                 "type": "TextBlock",
                 "text": f"Please provide: **{', '.join(p.replace('_', ' ') for p in missing_params)}**",
                 "wrap": True, "spacing": "Medium"
            }
        ],
        "actions": [
             # Let user reply in chat - state management for this is complex
             # Add Cancel button
             {
                "type": "Action.Submit",
                "title": "Cancel Request",
                "data": {"action": "cancel_request"}
             }
        ]
    }
    # Future: Add Input fields if feasible with bot state management
    return card 