from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, List

class MemoryInterface(ABC):
    """Abstract base class for user-specific memory storage."""

    @abstractmethod
    async def get_user_data(self, user_id: str) -> Dict[str, Any]:
        """Retrieves all data associated with a user ID."""
        pass

    @abstractmethod
    async def set_user_data(self, user_id: str, key: str, value: Any):
        """Sets a specific key-value pair for a user ID."""
        pass

    @abstractmethod
    async def get_user_value(self, user_id: str, key: str, default: Any = None) -> Any:
        """Gets a specific value for a user ID and key."""
        pass

    @abstractmethod
    async def delete_user_data(self, user_id: str, key: str):
        """Deletes a specific key for a user ID."""
        pass

    @abstractmethod
    async def clear_user_data(self, user_id: str):
        """Clears all data for a specific user ID."""
        pass

    # Optional: Add methods for finding keys if needed for features like reschedule_pending_digests
    # async def find_keys_with_pattern(self, pattern: str) -> list[str]:
    #     """Finds keys matching a pattern (e.g., 'user:*'). Specific implementation needed."""
    #     pass

    # --- Conversation Reference Storage ---
    @abstractmethod
    async def store_conversation_reference(self, ref: 'ConversationReference'):
        """Stores a conversation reference, typically keyed by conversation ID."""
        pass

    @abstractmethod
    async def get_conversation_reference(self, conversation_id: str) -> Optional['ConversationReference']:
        """Retrieves a specific conversation reference by its ID."""
        pass

    @abstractmethod
    async def delete_conversation_reference(self, conversation_id: str):
        """Deletes a specific conversation reference."""
        pass

    @abstractmethod
    async def list_conversation_references(self) -> List['ConversationReference']:
        """Lists all stored conversation references (implementation might be inefficient)."""
        pass 