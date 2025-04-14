import pytest
import json
from app.cards.general_cards import create_simple_message_card # Assuming this function exists

def test_create_simple_message_card():
    """Tests the structure and content of a simple message card."""
    title = "User Preferences"
    message = "- theme: dark\n- timezone: UTC"

    card_dict = create_simple_message_card(title=title, message=message)

    # Basic structure checks
    assert card_dict["type"] == "AdaptiveCard"
    assert card_dict["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert card_dict["version"] == "1.5" # Or your target version
    assert isinstance(card_dict["body"], list)

    # Content checks
    card_str = json.dumps(card_dict)
    assert title in card_str
    assert message in card_str # Check if the full message is present

    # Example: Check for specific TextBlocks
    has_title_block = False
    has_message_block = False
    for item in card_dict["body"]:
        if item.get("type") == "TextBlock":
            text = item.get("text", "")
            if title in text:
                has_title_block = True
            if message in text:
                has_message_block = True

    assert has_title_block, f"Card body should contain a TextBlock with the title: {title}"
    assert has_message_block, f"Card body should contain a TextBlock with the message: {message}"

# --- Add tests for other general card functions (e.g., error cards, suggestion cards) --- 