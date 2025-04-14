from typing import Dict

def create_alias_list_card(aliases: Dict[str, str]) -> dict:
    """Displays the user's command aliases."""
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "Your Command Aliases"}
        ]
    }
    if not aliases:
        card["body"].append({"type": "TextBlock", "text": "You have no aliases defined.", "isSubtle": True})
    else:
         # Use columns for better layout if many aliases
         facts = [{"title": f"`/{k}`", "value": f"`{v}`"} for k, v in aliases.items()]
         card["body"].append({"type": "FactSet", "facts": facts})

    card["body"].append({
         "type": "TextBlock", "text": "Use `/alias set <name> <command>` or `/alias delete <name>`.",
         "wrap": True, "isSubtle": True, "separator": True, "spacing": "Medium"
     })
    return card


def create_pref_list_card(preferences: Dict[str, Any]) -> dict:
    """Displays the user's preferences."""
    from tools.general_tools import ALLOWED_PREFS # Get descriptions
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "Your Preferences"}
        ]
    }
    if not preferences:
         card["body"].append({"type": "TextBlock", "text": "You have no preferences set.", "isSubtle": True})
    else:
        # Filter to only show allowed/known prefs
        prefs_to_display = {k:v for k,v in preferences.items() if k in ALLOWED_PREFS}
        facts = [{"title": k.replace('_', ' ').title(), "value": f"`{v}`"} for k, v in prefs_to_display.items()]
        if not facts:
            card["body"].append({"type": "TextBlock", "text": "You have no preferences set.", "isSubtle": True})
        else:
            card["body"].append({"type": "FactSet", "facts": facts})

    card["body"].append({
         "type": "TextBlock", "text": "Use `/pref set <name> <value>`, `/pref get <name>`, or `/pref digest on|off`.",
         "wrap": True, "isSubtle": True, "separator": True, "spacing": "Medium"
     })
    # Maybe add buttons for common actions like toggling digest
    digest_enabled = preferences.get("digest_enabled", False)
    card.setdefault("actions", []).append({
         "type": "Action.Submit",
         "title": f"Turn Digest {'Off' if digest_enabled else 'On'}",
         "data": {"msteams": {"type": "messageBack", "text": f"/pref digest {'off' if digest_enabled else 'on'}"}}
     })
    return card 