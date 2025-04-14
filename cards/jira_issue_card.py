from typing import Dict, Any

def create_jira_issue_card(result_data: Dict[str, Any]) -> dict:
    """Generates an Adaptive Card for a newly created Jira issue (e.g., for cherry-pick)."""
    issue_key = result_data.get("issue_key"); issue_url = result_data.get("issue_url")
    summary = result_data.get("summary", "Jira Issue Created"); status_color = "good"
    details = {
        "Issue": f"[{issue_key}]({issue_url})" if issue_key and issue_url else issue_key,
        "Repository": result_data.get("repo_name"),
        "Commit SHA": f"`{result_data.get('commit_sha')}`" if result_data.get('commit_sha') else None,
        "Target Branch": f"`{result_data.get('target_branch')}`" if result_data.get('target_branch') else None,
    }
    filtered_details = {k: v for k, v in details.items() if v is not None}
    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": f"✅ Jira Issue Created: {issue_key or ''}", "color": status_color},
            {"type": "TextBlock", "text": summary, "wrap": True, "spacing": "Small"},
            {"type": "FactSet", "facts": [{"title": k, "value": str(v)} for k, v in filtered_details.items()], "spacing": "Medium"}
        ],
        "actions": []
    }
    if issue_url: card["actions"].append({"type": "Action.OpenUrl", "title": "View Issue in Jira", "url": issue_url})
    return card 