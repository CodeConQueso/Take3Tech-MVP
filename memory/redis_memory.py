import logging
import json
from typing import Dict, Any, Optional, List
import redis.asyncio as redis # Use async redis client
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError # Import specific Redis errors
from botbuilder.schema import ConversationReference # Added
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type # Added for retries

from .interface import MemoryInterface
from utils.config import settings # To get REDIS_URL

logger = logging.getLogger(__name__)

# Define a specific prefix for conversation reference keys
CONV_REF_PREFIX = "conv_ref:"
USER_PREFIX = "chatops_user:" # Existing prefix for user data

# --- Retry configuration ---
# Retry on specific Redis network/server errors
RETRYABLE_REDIS_EXCEPTIONS = (RedisConnectionError, RedisTimeoutError, RedisError, OSError) 

class RedisMemory(MemoryInterface):
    """Redis-backed persistent memory store with retry logic."""
    def __init__(self, redis_url: str):
        if not redis_url:
            raise ValueError("Redis URL must be provided to initialize RedisMemory.")
        try:
            self.pool = redis.ConnectionPool.from_url(redis_url, decode_responses=True, health_check_interval=30)
            self.redis_client = redis.Redis(connection_pool=self.pool)
            logger.info(f"Initialized RedisMemory store connection pool to {redis_url.split('@')[-1]}")
        except Exception as e:
            logger.error(f"Failed to create Redis connection pool at {redis_url.split('@')[-1]}: {e}", exc_info=True)
            raise ConnectionError(f"Could not connect to Redis: {e}") from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=3),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def _check_connection(self):
        """Checks Redis connection, retrying on failures."""
        try:
            await self.redis_client.ping()
            logger.debug("Redis PING successful.")
        except RETRYABLE_REDIS_EXCEPTIONS as e:
            logger.warning(f"Redis connection check failed: {e}. Retrying...")
            raise # Reraise for tenacity
        except Exception as e:
            logger.error(f"Unexpected Redis error during PING: {e}")
            # Don't automatically retry unknown errors, raise immediately
            raise ConnectionError("Unexpected Redis error") from e 

    def _user_key(self, user_id: str) -> str:
        """Generates the Redis key for a user's hash data."""
        safe_user_id = user_id.replace(":", "_")
        return f"{USER_PREFIX}{safe_user_id}"

    def _conv_ref_key(self, conversation_id: str) -> str:
        """Generates the Redis key for a conversation reference string."""
        safe_conv_id = conversation_id.replace(":", "_") 
        return f"{CONV_REF_PREFIX}{safe_conv_id}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=5),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def get_user_data(self, user_id: str) -> Dict[str, Any]:
        if not user_id: return {}
        key = self._user_key(user_id)
        try:
            # No need to call _check_connection explicitly here,
            # the operation itself will test the connection and retry if needed.
            data = await self.redis_client.hgetall(key)
            deserialized_data = {}
            for k, v in data.items():
                try: deserialized_data[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError): deserialized_data[k] = v
            return deserialized_data
        except RETRYABLE_REDIS_EXCEPTIONS as e:
            logger.warning(f"Redis error getting data for user {user_id} (key: {key}): {e}. Retrying...")
            raise # Reraise for tenacity
        except Exception as e:
            logger.error(f"Unexpected error getting data for user {user_id}: {e}", exc_info=True)
            # Propagate unexpected errors immediately
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=5),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def set_user_data(self, user_id: str, key: str, value: Any):
        if not user_id: return
        redis_key = self._user_key(user_id)
        try:
            if isinstance(value, (dict, list, tuple, set)): value_str = json.dumps(value)
            elif isinstance(value, bool): value_str = json.dumps(value)
            else: value_str = str(value)
            await self.redis_client.hset(redis_key, key, value_str)
        except RETRYABLE_REDIS_EXCEPTIONS as e: 
            logger.warning(f"Redis error setting data for user {user_id} (key: {key}): {e}. Retrying...")
            raise
        except Exception as e: 
            logger.error(f"Unexpected error setting data for user {user_id} (key: {key}): {e}", exc_info=True)
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=5),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def get_user_value(self, user_id: str, key: str, default: Any = None) -> Any:
        if not user_id: return default
        redis_key = self._user_key(user_id)
        try:
            value_str = await self.redis_client.hget(redis_key, key)
            if value_str is None: return default
            try:
                if value_str.startswith( ('{', '[', '"')) or value_str in ['true', 'false']:
                     return json.loads(value_str)
                return value_str
            except (json.JSONDecodeError, TypeError): return value_str
        except RETRYABLE_REDIS_EXCEPTIONS as e:
            logger.warning(f"Redis error getting value for user {user_id} (key: {key}): {e}. Retrying...")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting value for user {user_id} (key: {key}): {e}", exc_info=True)
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=5),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def delete_user_data(self, user_id: str, key: str):
        if not user_id: return
        redis_key = self._user_key(user_id)
        try:
            await self.redis_client.hdel(redis_key, key)
        except RETRYABLE_REDIS_EXCEPTIONS as e: 
            logger.warning(f"Redis error deleting data for user {user_id} (key: {key}): {e}. Retrying...")
            raise
        except Exception as e: 
            logger.error(f"Unexpected error deleting data for user {user_id} (key: {key}): {e}", exc_info=True)
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=5),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def clear_user_data(self, user_id: str):
        if not user_id: return
        redis_key = self._user_key(user_id)
        try:
            await self.redis_client.delete(redis_key)
            logger.info(f"Redis CLEARED user data for user={user_id}")
        except RETRYABLE_REDIS_EXCEPTIONS as e: 
            logger.warning(f"Redis error clearing data for user {user_id}: {e}. Retrying...")
            raise
        except Exception as e: 
            logger.error(f"Unexpected error clearing data for user {user_id}: {e}", exc_info=True)
            raise

    # --- Conversation Reference Methods (with retries) ---
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=5),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def store_conversation_reference(self, ref: ConversationReference):
        if not ref or not ref.conversation or not ref.conversation.id:
            logger.warning("Attempted to store invalid ConversationReference.")
            return
        
        key = self._conv_ref_key(ref.conversation.id)
        try:
            ref_dict = ref.as_dict()
            ref_json = json.dumps(ref_dict)
            await self.redis_client.set(key, ref_json)
            logger.debug(f"Stored conversation reference for conv_id: {ref.conversation.id}")
        except RETRYABLE_REDIS_EXCEPTIONS as e:
            logger.warning(f"Redis error storing conversation reference {ref.conversation.id}: {e}. Retrying...")
            raise
        except Exception as e:
            logger.error(f"Unexpected error storing reference {ref.conversation.id}: {e}", exc_info=True)
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=5),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def get_conversation_reference(self, conversation_id: str) -> Optional[ConversationReference]:
        if not conversation_id:
            return None
        key = self._conv_ref_key(conversation_id)
        try:
            ref_json = await self.redis_client.get(key)
            if not ref_json:
                logger.debug(f"Conversation reference not found for conv_id: {conversation_id}")
                return None
            
            ref_dict = json.loads(ref_json)
            ref = ConversationReference(
                channel_id=ref_dict.get('channelId'),
                service_url=ref_dict.get('serviceUrl'),
                conversation=ref_dict.get('conversation'),
                user=ref_dict.get('user'),
                bot=ref_dict.get('bot'),
                activity_id=ref_dict.get('activityId') 
            )
            if not all([ref.channel_id, ref.service_url, ref.conversation, ref.conversation.id, ref.bot, ref.bot.id]):
                logger.error(f"Retrieved conversation reference for {conversation_id} is incomplete/invalid: {ref_dict}")
                # Don't return invalid ref, let retry handle potential transient data issues, but fail if persistent
                # Or handle corrupt data explicitly?
                # For now, return None if data seems corrupt after successful fetch.
                return None 

            logger.debug(f"Retrieved conversation reference for conv_id: {conversation_id}")
            return ref
        except RETRYABLE_REDIS_EXCEPTIONS as e:
            logger.warning(f"Redis error getting conversation reference {conversation_id}: {e}. Retrying...")
            raise
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            # Don't retry JSON errors, likely indicates corrupt data
            logger.error(f"Error deserializing/reconstructing reference {conversation_id}: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting reference {conversation_id}: {e}", exc_info=True)
            raise # Reraise unexpected errors

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=0.5, max=5),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def delete_conversation_reference(self, conversation_id: str):
        if not conversation_id: return
        key = self._conv_ref_key(conversation_id)
        try:
            deleted_count = await self.redis_client.delete(key)
            if deleted_count > 0:
                 logger.info(f"Deleted conversation reference for conv_id: {conversation_id}")
            else:
                 logger.debug(f"Attempted to delete non-existent reference for conv_id: {conversation_id}")
        except RETRYABLE_REDIS_EXCEPTIONS as e:
            logger.warning(f"Redis error deleting conversation reference {conversation_id}: {e}. Retrying...")
            raise
        except Exception as e:
            logger.error(f"Unexpected error deleting reference {conversation_id}: {e}", exc_info=True)
            raise

    @retry(
        stop=stop_after_attempt(2), # Fewer retries for potentially slow scan
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(RETRYABLE_REDIS_EXCEPTIONS),
        reraise=True
    )
    async def list_conversation_references(self) -> List[ConversationReference]:
        """Lists all conversation references. Retries on error. Warning: May be slow."""
        references = []
        try:
            cursor = '0'
            while cursor != 0:
                cursor, keys = await self.redis_client.scan(cursor=cursor, match=f"{CONV_REF_PREFIX}*", count=100)
                if keys:
                    # Fetch values in batches
                    values = await self.redis_client.mget(keys)
                    for i, ref_json in enumerate(values):
                        if ref_json:
                            try:
                                ref_dict = json.loads(ref_json)
                                ref = ConversationReference(
                                    channel_id=ref_dict.get('channelId'),
                                    service_url=ref_dict.get('serviceUrl'),
                                    conversation=ref_dict.get('conversation'),
                                    user=ref_dict.get('user'),
                                    bot=ref_dict.get('bot'),
                                    activity_id=ref_dict.get('activityId') 
                                )
                                if all([ref.channel_id, ref.service_url, ref.conversation, ref.conversation.id, ref.bot, ref.bot.id]):
                                    references.append(ref)
                                else:
                                    logger.warning(f"Skipping incomplete reference stored under key {keys[i]}")
                            except (json.JSONDecodeError, TypeError, KeyError) as e:
                                # Don't retry scan on data deserialization error, just log and skip entry
                                logger.error(f"Error deserializing reference from key {keys[i]} during list: {e}")
            logger.info(f"Listed {len(references)} conversation references from Redis.")
            return references
        except RETRYABLE_REDIS_EXCEPTIONS as e:
            logger.warning(f"Redis error listing conversation references: {e}. Retrying...")
            raise
        except Exception as e:
            logger.error(f"Unexpected error listing references: {e}", exc_info=True)
            raise # Reraise unexpected errors

    # Add close method for graceful shutdown
    async def close(self):
         if self.redis_client:
             try:
                 await self.redis_client.close()
                 await self.pool.disconnect()
                 logger.info("Closed Redis connection pool.")
             except Exception as e:
                 logger.error(f"Error closing Redis connection: {e}") 