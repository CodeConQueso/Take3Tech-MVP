from typing import Dict, Any
import dateutil.parser

def create_explanation_card(result_data: Dict[str, Any]) -> dict:
    """Generates an Adaptive Card for the /explain command result."""
    item_type = result_data.get("item_type", "Item")
    item_key = result_data.get("item_key", "N/A")
    explanation = result_data.get("explanation", "Could not generate explanation.")
    item_details = result_data.get("item_details", {})
    warning = result_data.get("warning")

    # Extract common fields
    title = item_details.get("title") or item_details.get("summary", "N/A")
    url = item_details.get("html_url") or item_details.get("url")
    status = item_details.get("state") or item_details.get("status", "N/A")
    if isinstance(status, str): status = status.capitalize()
    author = item_details.get("user_login") or item_details.get("reporter")
    assignee = item_details.get("assignee")
    if isinstance(assignee, list): assignee = ", ".join(assignee) or "None"

    created_at = item_details.get("created_at") or item_details.get("created")
    created_formatted = ""
    if created_at:
         try:
             dt_obj = dateutil.parser.isoparse(created_at)
             date_str = dt_obj.strftime('%Y-%m-%dT%H:%M:%SZ')
             created_formatted = f"{{{{DATE({date_str}, SHORT)}}}} {{{{TIME({date_str})}}}}"
         except: created_formatted = str(created_at)

    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": f"🤔 Explanation for {item_type.upper()} {item_key}"},
            {"type": "TextBlock", "text": f"**Title:** {title}", "wrap": True, "spacing":"Small"},
            {"type": "TextBlock", "text": "**AI Summary:**", "wrap": True, "weight":"Bolder", "spacing":"Medium"},
            {"type": "TextBlock", "text": explanation, "wrap": True }
        ]
    }
    if warning: card["body"].append({"type": "TextBlock", "text": f"⚠️ {warning}", "wrap": True, "color": "warning", "spacing":"Small"})

    facts = [
        {"title": "Status", "value": str(status)},
        {"title": "Author/Reporter", "value": author or "N/A"},
        {"title": "Assignee(s)", "value": assignee or "N/A"},
        {"title": "Created", "value": created_formatted},
    ]
    if item_type == 'pr': facts.append({"title": "Changes", "value": f"+{item_details.get('additions', '?')} / -{item_details.get('deletions', '?')} ({item_details.get('changed_files', '?')} files)"})

    card["body"].append({"type": "FactSet", "spacing": "Medium", "facts": [f for f in facts if f["value"] and f["value"] not in ["N/A", "None"]]})
    if url: card["actions"] = [{"type": "Action.OpenUrl", "title": f"View {item_type.upper()} Details", "url": url}]
    return card 