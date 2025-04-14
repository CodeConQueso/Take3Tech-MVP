import pytest
from app.cards.jira_cards import create_jira_issue_summary_card # Assuming this function exists

def test_create_jira_issue_summary_card():
    """Tests the structure and content of the Jira issue summary card."""
    issue_key = "PROJ-123"
    issue_summary = "Fix the login button bug"
    issue_url = "https://test.atlassian.net/browse/PROJ-123"
    status = "In Progress"
    assignee_name = "Test User"

    card_dict = create_jira_issue_summary_card(
        issue_key=issue_key,
        issue_summary=issue_summary,
        issue_url=issue_url,
        status=status,
        assignee_name=assignee_name
    )

    # Basic structure checks
    assert card_dict["type"] == "AdaptiveCard"
    assert card_dict["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert card_dict["version"] == "1.5" # Or your target version
    assert isinstance(card_dict["body"], list)

    # Content checks (adapt based on your actual card layout)
    # Convert to string for easier searching
    import json
    card_str = json.dumps(card_dict)

    assert issue_key in card_str
    assert issue_summary in card_str
    assert issue_url in card_str # Check if URL is used in an action
    assert status in card_str
    assert assignee_name in card_str

    # Check for specific elements if needed
    # Example: Check for an OpenUrl action pointing to the issue
    has_open_url_action = False
    if "actions" in card_dict:
        for action in card_dict["actions"]:
            if action.get("type") == "Action.OpenUrl" and action.get("url") == issue_url:
                has_open_url_action = True
                break
    assert has_open_url_action, f"Card should have an OpenUrl action linking to {issue_url}"

    # Example: Check for a TextBlock containing the summary
    has_summary_textblock = False
    for item in card_dict["body"]:
        if item.get("type") == "TextBlock" and issue_summary in item.get("text", ""):
            has_summary_textblock = True
            break
    assert has_summary_textblock, f"Card body should contain a TextBlock with the summary: {issue_summary}"

# --- Add tests for other Jira card functions --- 