from typing import Dict, Any

def create_perplexity_card(result_data: Dict[str, Any]) -> dict:
    """Generates an Adaptive Card for Perplexity search results."""
    query = result_data.get("query", "Your search")
    answer = result_data.get("answer", "No answer received.")
    answer_preview = (answer[:1000] + "...") if len(answer) > 1000 else answer

    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": f"💡 Perplexity Search Results"},
            {"type": "TextBlock", "text": f"**Query:** {query}", "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": "Answer:", "wrap": True, "weight": "Bolder", "spacing": "Medium"},
            {"type": "TextBlock", "text": answer_preview, "wrap": True, "id": "answerPreview"}
        ],
        "actions": []
    }
    # Simple toggle visibility for longer answers
    if len(answer) > 1000:
         card["body"].append({"type": "TextBlock", "text": answer, "wrap": True, "id": "answerFull", "isVisible": False})
         card["actions"].append({
            "type": "Action.ToggleVisibility", "title": "Show More/Less",
            "targetElements": ["answerPreview", "answerFull"]
         })
    return card 