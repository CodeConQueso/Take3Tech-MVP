import logging
import logging.handlers
import json
import os
from typing import Dict, Any
from datetime import datetime # For precise timestamps
from .config import settings # Import settings for config

# Ensure log directory exists
log_dir = os.path.dirname(settings.AUDIT_LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure dedicated audit logger
# Use JSON formatter for structured logging
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage() # The core message (should be JSON string from our logger)
        }
        # Attempt to parse the core message as JSON to merge structured details
        try:
            core_message_dict = json.loads(record.getMessage())
            # Merge, preferring details from the core message if keys overlap (e.g., user_id)
            log_record.update(core_message_dict)
            log_record["message"] = log_record.get("event", "audit_event") # Use event name as message
        except json.JSONDecodeError:
            # If message wasn't JSON, keep it as is
            pass
        except Exception: # Catch other potential errors during parsing
             pass # Keep original message if merging fails

        # Ensure complex objects are represented as strings if not handled above
        return json.dumps(log_record, default=str) # Use default=str for non-serializable


# audit_log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s') # Simple format
audit_log_formatter = JsonFormatter('%(asctime)s.%(msecs)03dZ', '%Y-%m-%dT%H:%M:%S') # ISO format with millis
audit_logger = logging.getLogger("AuditLogger")
audit_logger.setLevel(logging.INFO) # Audit logs should generally capture INFO level
audit_logger.propagate = False # Prevent audit logs from going to the main root logger/console

# Audit File Handler (Rotating)
audit_file_handler = logging.handlers.RotatingFileHandler(
    settings.AUDIT_LOG_FILE,
    maxBytes=10*1024*1024, # 10 MB per audit file
    backupCount=10 # Keep 10 backup audit files
)
audit_file_handler.setFormatter(audit_log_formatter)
audit_logger.addHandler(audit_file_handler)

# Mask sensitive data (simple example, enhance as needed)
SENSITIVE_KEYS = {"token", "password", "apikey", "secret", "jira_api_token", "github_token", "octopus_api_key", "perplexity_api_key", "bot_base_url", "microsoft_app_password"}

def mask_sensitive_data(data: Any) -> Any:
    """Recursively masks sensitive keys in a dictionary or list."""
    if isinstance(data, dict):
        cleaned_dict = {}
        for k, v in data.items():
            key_lower = str(k).lower()
            if any(sens_key in key_lower for sens_key in SENSITIVE_KEYS):
                 cleaned_dict[k] = "**** MASKED ****"
            else:
                 cleaned_dict[k] = mask_sensitive_data(v)
        return cleaned_dict
    elif isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    elif isinstance(data, str):
         # Basic check for string values that look like secrets (less reliable)
         # if any(sens_key in data.lower() for sens_key in SENSITIVE_KEYS) and len(data) > 8:
         #     return "**** MASKED ****"
         return data
    return data

class AuditLogger:
    """Provides a structured way to log audit events as JSON."""

    def log(self, event: str, user_id: str | None = None, **kwargs: Any):
        """
        Logs an audit event.

        Args:
            event (str): The name of the event (e.g., 'command_received', 'tool_executed').
            user_id (str | None): The ID of the user associated with the event. Can be None for system events.
            **kwargs: Additional structured data about the event. Sensitive data within kwargs will be masked.
        """
        log_entry = {
            "event": event,
            "user_id": user_id or "SYSTEM", # Use SYSTEM if no specific user
            "details": mask_sensitive_data(kwargs) # Mask details before logging
        }
        try:
            # Log the dict as a JSON string - JsonFormatter will handle structure
            audit_logger.info(json.dumps(log_entry))
        except TypeError as e:
            # Fallback if JSON serialization fails (should be rare with default=str)
            fallback_details = str(log_entry['details']) # Convert details to string
            audit_logger.error(f"Failed to serialize audit log entry to JSON: {e}. Entry: {event}, User: {log_entry['user_id']}, Details: {fallback_details}")

# Instantiate the logger
audit = AuditLogger()

# Example Usage:
# audit.log("command_received", user_id, raw_text=original_text, channel_id=activity.conversation.id)
# audit.log("permission_check", user_id, tool=tool_name, allowed=True) 