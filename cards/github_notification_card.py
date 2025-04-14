from typing import Dict, Any

def create_github_notification_card(event_data: Dict[str, Any]) -> dict | None:
    """Generates card for GitHub webhook events (e.g., workflow run)."""
    # Example for 'workflow_run' event
    if event_data.get("action") == "completed" and "workflow_run" in event_data:
        run = event_data["workflow_run"]; repo = event_data["repository"]
        status = run.get("conclusion", "unknown"); workflow_name = event_data["workflow"].get("name", "Workflow")
        actor = run.get("actor", {}).get("login", "N/A"); branch = run.get("head_branch", "N/A")
        commit_sha = run.get("head_sha", "")[:7]; run_url = run.get("html_url", "#")

        status_color = "default"; icon = "❓"
        if status == "success": status_color, icon = "good", "✅"
        elif status in ["failure", "cancelled", "timed_out"]: status_color, icon = "attention", "❌"
        elif status == "skipped": status_color, icon = "warning", " RARROW" # Skip icon
        elif status == "action_required": status_color, icon = "warning", "❗"

        title = f"{icon} GitHub Action: {workflow_name} {status.capitalize()}"
        card = {
            "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
            "body": [
                {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": title, "color": status_color},
                {"type": "FactSet", "spacing": "Medium", "facts": [
                    {"title": "Repository", "value": f"[{repo.get('full_name', 'N/A')}]({repo.get('html_url', '#')})"},
                    {"title": "Branch", "value": f"`{branch}`"}, {"title": "Commit", "value": f"`{commit_sha}`"},
                    {"title": "Triggered By", "value": actor}, {"title": "Status", "value": status.capitalize()},
                ]}
            ],
            "actions": [{"type": "Action.OpenUrl", "title": "View Workflow Run", "url": run_url}]
        }
        # if status == "failure": card["actions"].append({ ... Action.Submit data for retry tool ... })
        return card

    # Add handlers for other events (PRs, issues, etc.)
    # logger.warning(f"No card handler for GitHub webhook event action: {event_data.get('action')}")
    return None 