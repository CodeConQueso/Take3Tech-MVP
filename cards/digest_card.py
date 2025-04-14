import logging
from typing import Dict, Any, List
from datetime import datetime
import dateutil.parser

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_SECTION = 5 # Limit items shown directly in the card

def create_digest_card(digest_data: Dict[str, List[Dict[str, Any]]], errors: List[str]) -> Dict[str, Any]:
    """Creates an Adaptive Card for the daily digest."""
    
    card_body = [
        {
            "type": "TextBlock",
            "text": f"Your Daily Digest - {datetime.now().strftime('%Y-%m-%d')}",
            "weight": "Bolder",
            "size": "Medium"
        }
    ]
    
    # --- Handle Errors ---
    if errors:
        error_items = [
            {
                "type": "TextBlock",
                "text": f"- {error}",
                "wrap": True, 
                "color": "Attention"
            }
            for error in errors
        ]
        card_body.extend([
             {
                "type": "TextBlock",
                "text": "⚠️ Issues retrieving some digest items:",
                "weight": "Bolder",
                "color": "Attention",
                "spacing": "Medium"
            },
             {
                "type": "Container",
                "items": error_items
            }
        ])
    
    # --- Assigned GitHub PRs ---
    prs = digest_data.get("prs", [])
    card_body.append({
        "type": "TextBlock",
        "text": f"Open GitHub PRs Assigned to You ({len(prs)})",
        "weight": "Bolder",
        "spacing": "Medium"
    })
    if prs:
        pr_items = []
        for i, pr in enumerate(prs):
            if i >= MAX_ITEMS_PER_SECTION:
                pr_items.append({
                    "type": "TextBlock",
                    "text": f"...and {len(prs) - MAX_ITEMS_PER_SECTION} more.",
                    "weight": "Lighter"
                })
                break
            pr_items.append({
                 "type": "TextBlock",
                 "text": f"- [#{pr.get('number')}: {pr.get('title', 'N/A')}]({pr.get('url', '#')}) ({pr.get('repo_name', '?')})",
                 "wrap": True
            })
        card_body.append({"type": "Container", "items": pr_items})
    else:
        card_body.append({"type": "TextBlock", "text": "No open assigned PRs found.", "isSubtle": True})
        
    # --- Assigned Jira Issues ---
    jira_issues = digest_data.get("jira_issues", [])
    card_body.append({
        "type": "TextBlock",
        "text": f"Open Jira Issues Assigned to You ({len(jira_issues)})",
        "weight": "Bolder",
        "spacing": "Medium"
    })
    if jira_issues:
        jira_items = []
        for i, issue in enumerate(jira_issues):
            if i >= MAX_ITEMS_PER_SECTION:
                jira_items.append({
                    "type": "TextBlock",
                    "text": f"...and {len(jira_issues) - MAX_ITEMS_PER_SECTION} more.",
                    "weight": "Lighter"
                })
                break
            
            # Format updated time nicely if possible
            updated_str = issue.get('updated', '')
            updated_display = updated_str
            try:
                updated_dt = dateutil.parser.isoparse(updated_str)
                updated_display = f"{updated_dt:%Y-%m-%d %H:%M} UTC"
            except ValueError:
                pass 
            
            jira_items.append({
                 "type": "TextBlock",
                 "text": f"- [{issue.get('key')}: {issue.get('summary', 'N/A')}]({issue.get('url', '#')}) ({issue.get('status', '?')}, Prio: {issue.get('priority', '?')}, Updated: {updated_display})",
                 "wrap": True
            })
        card_body.append({"type": "Container", "items": jira_items})
    else:
        card_body.append({"type": "TextBlock", "text": "No open assigned Jira issues found.", "isSubtle": True})
        
    # --- Add Footer/Actions (Optional) ---
    card_body.append({
        "type": "TextBlock",
        "text": "Manage digest settings with `/pref`",
        "isSubtle": True,
        "separator": True,
        "spacing": "Medium"
    })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": card_body
    } 