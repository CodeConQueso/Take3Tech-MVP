def create_about_card() -> dict:
    """Generates the Adaptive Card JSON explaining the bot's purpose and capabilities."""
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock", "size": "Medium", "weight": "Bolder",
                "text": "ChatOps Bot Assistant (Enhanced)"
            },
            {
                "type": "TextBlock",
                "text": "I understand natural language commands to help with your development workflow. You can ask me to summarize PRs, trigger deployments, explain items, manage settings, and more.",
                "wrap": True
            },
            {
                "type": "TextBlock", "text": "Example Commands:",
                "wrap": True, "spacing": "Medium", "weight": "Bolder"
            },
            { "type": "TextBlock", "text": "- `Summarize PR 123 in octocat/Spoon-Knife`", "wrap": True },
            { "type": "TextBlock", "text": "- `Explain JIRA-456`", "wrap": True },
            { "type": "TextBlock", "text": "- `Deploy MyWebApp v1.2 to Staging`", "wrap": True },
            { "type": "TextBlock", "text": "- `What are the latest CI/CD trends?`", "wrap": True },
            { "type": "TextBlock", "text": "- `/suggest` - Ask me for relevant actions.", "wrap": True },
            { "type": "TextBlock", "text": "- `/pref list` - View or manage preferences (defaults, digest, linked accounts).", "wrap": True },
            { "type": "TextBlock", "text": "- `/alias list` - View or manage command shortcuts.", "wrap": True },
            { "type": "TextBlock", "text": "- `/about` - Show this message.", "wrap": True },
            {
                 "type": "TextBlock",
                 "text": "I use your context (last repo/project) if you omit details. Type `/about` anytime.",
                 "wrap": True, "spacing": "Medium", "isSubtle": True
             }
        ],
         "actions": [ # Add button for suggest
             {
                 "type": "Action.Submit",
                 "title": "Suggest Actions",
                 "data": { "msteams": { "type": "messageBack", "text": "/suggest" } }
             }
         ]
    } 