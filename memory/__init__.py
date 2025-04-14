import logging
from utils.config import settings
from .interface import MemoryInterface
from .in_memory import InMemoryMemory

logger = logging.getLogger(__name__)

current_memory: MemoryInterface

if settings.REDIS_URL:
    try:
        # Dynamically import RedisMemory only if needed and available
        from .redis_memory import RedisMemory
        import redis # Check if library is installed
        current_memory = RedisMemory(settings.REDIS_URL)
        logger.info("Using Redis memory store.")
    except ImportError:
        logger.error("REDIS_URL is set, but 'redis' package not found. Falling back to in-memory store.")
        logger.error("Install redis dependencies: pip install redis[hiredis]")
        current_memory = InMemoryMemory()
    except ConnectionError as e:
         logger.error(f"Failed to connect to Redis specified by REDIS_URL. Falling back to in-memory store. Error: {e}")
         current_memory = InMemoryMemory()
    except Exception as e:
        logger.error(f"Unexpected error initializing Redis memory store. Falling back to in-memory store. Error: {e}", exc_info=True)
        current_memory = InMemoryMemory()
else:
    logger.info("Using in-memory memory store.")
    current_memory = InMemoryMemory()

# Export the chosen memory implementation
__all__ = ["current_memory", "MemoryInterface"] 