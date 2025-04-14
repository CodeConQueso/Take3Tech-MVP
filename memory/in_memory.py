import logging
from collections import defaultdict
from typing import Dict, Any, Optional, List
from botbuilder.schema import ConversationReference
from .interface import MemoryInterface

logger = logging.getLogger(__name__)

class InMemoryMemory(MemoryInterface):
    """
    Simple in-memory store using defaultdict. NOT persistent.
    """
    def __init__(self):
        self.user_store: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self.conv_ref_store: Dict[str, ConversationReference] = {}
        logger.info("Initialized InMemoryMemory store.")

    async def get_user_data(self, user_id: str) -> Dict[str, Any]:
        if not user_id: return {}
        return self.user_store[user_id].copy()

    async def set_user_data(self, user_id: str, key: str, value: Any):
        if not user_id: return
        self.user_store[user_id][key] = value

    async def get_user_value(self, user_id: str, key: str, default: Any = None) -> Any:
        if not user_id: return default
        return self.user_store[user_id].get(key, default)

    async def delete_user_data(self, user_id: str, key: str):
        if not user_id: return
        if user_id in self.user_store and key in self.user_store[user_id]:
            del self.user_store[user_id][key]

    async def clear_user_data(self, user_id: str):
        if not user_id: return
        if user_id in self.user_store:
            del self.user_store[user_id]
            logger.info(f"InMem CLEARED user={user_id}")

    # --- Conversation Reference Methods ---
    async def store_conversation_reference(self, ref: ConversationReference):
        if ref and ref.conversation and ref.conversation.id:
            self.conv_ref_store[ref.conversation.id] = ref
            logger.debug(f"Stored in-memory conv ref for {ref.conversation.id}")
        else:
            logger.warning("Attempted to store invalid ConversationReference in memory.")

    async def get_conversation_reference(self, conversation_id: str) -> Optional[ConversationReference]:
        ref = self.conv_ref_store.get(conversation_id)
        if ref:
            logger.debug(f"Retrieved in-memory conv ref for {conversation_id}")
        return ref

    async def delete_conversation_reference(self, conversation_id: str):
        if conversation_id in self.conv_ref_store:
            del self.conv_ref_store[conversation_id]
            logger.info(f"Deleted in-memory conv ref for {conversation_id}")

    async def list_conversation_references(self) -> List[ConversationReference]:
        refs = list(self.conv_ref_store.values())
        logger.info(f"Listed {len(refs)} in-memory conversation references.")
        return refs 