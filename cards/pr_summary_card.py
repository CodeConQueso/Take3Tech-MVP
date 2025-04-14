from typing import Dict, Any
import dateutil.parser

def create_pr_summary_card_from_data(pr_data: Dict[str, Any]) -> dict:
    """Generates an Adaptive Card summarizing a GitHub PR from a data dictionary."""
    repo_name = pr_data.get("repo_name", "N/A"); pr_number = pr_data.get("pr_number", "N/A")
    title = pr_data.get("title", "Pull Request Summary"); state = pr_data.get("state", "unknown").lower()
    is_draft = pr_data.get("draft", False); is_merged = pr_data.get("merged", False)
    html_url = pr_data.get("html_url")

    status_color = "default"; status_text = f"#{pr_number} {state.capitalize()}"
    if state == "open": status_color, status_text = ("warning", status_text + " (Draft)") if is_draft else ("accent", status_text)
    elif state == "closed": status_color, status_text = ("good", status_text + " (Merged)") if is_merged else ("attention", status_text + " (Closed)")
    elif state == "merged": status_color, status_text = "good", f"#{pr_number} Merged"

    body_preview = (pr_data.get("body", "No description.")[:300] + "...") if len(pr_data.get("body", "")) > 300 else pr_data.get("body", "No description.")
    created_at_str = pr_data.get("created_at"); created_at_formatted = ""
    if created_at_str:
        try:
            dt_obj = dateutil.parser.isoparse(created_at_str); date_str = dt_obj.strftime('%Y-%m-%dT%H:%M:%SZ')
            created_at_formatted = f"{{{{DATE({date_str}, SHORT)}}}} {{{{TIME({date_str})}}}}"
        except: created_at_formatted = created_at_str

    facts = [
        {"title": "Author", "value": pr_data.get("user_login")},
        {"title": "Branch", "value": f"`{pr_data.get('head_ref', '?')}` → `{pr_data.get('base_ref', '?')}`"},
        {"title": "Assignees", "value": ", ".join(pr_data.get("assignees", [])) or "None"},
        {"title": "Reviewers", "value": ", ".join(pr_data.get("reviewers", [])) or "None"},
        {"title": "Created", "value": created_at_formatted},
        {"title": "Commits", "value": str(pr_data.get("commits", "?"))},
        {"title": "Changes", "value": f"+{pr_data.get('additions', '?')} / -{pr_data.get('deletions', '?')} ({pr_data.get('changed_files', '?')} files)"},
    ]
    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
        "body": [
            {"type": "Container", "items": [
                {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": title, "wrap": True},
                {"type": "TextBlock", "text": f"[{repo_name} #{pr_number}]({html_url or '#'})", "isSubtle": True, "spacing": "None"}
            ]},
            {"type": "TextBlock", "text": status_text, "weight": "Bolder", "color": status_color, "spacing": "Small"},
            {"type": "TextBlock", "text": "Description Preview:", "weight": "Bolder", "spacing": "Medium"},
            {"type": "TextBlock", "text": body_preview, "wrap": True},
            {"type": "FactSet", "spacing": "Medium", "facts": [f for f in facts if f.get("value")]}
        ], "actions": []
    }
    if html_url: card["actions"].append({"type": "Action.OpenUrl", "title": "View PR on GitHub", "url": html_url})
    # Add explain action
    card["actions"].append({"type": "Action.Submit", "title": "Explain PR", "data": {"msteams": {"type": "messageBack", "text": f"/explain {repo_name}#{pr_number}"}}})
    return card 