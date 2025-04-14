import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import json

from botbuilder.schema import ConversationReference, ChannelAccount, ConversationAccount
from redis.asyncio import Redis
from redis.exceptions import RedisError

# Module to test
from memory.redis_memory import RedisMemory

# --- Fixtures ---

@pytest_asyncio.fixture
async def mock_redis_client(mocker):
    """Fixture providing a mocked redis.asyncio Redis client."""
    mock_client = AsyncMock(spec=Redis)
    # Default mock implementations (can be overridden in tests)
    mock_client.get = AsyncMock(return_value=None)
    mock_client.set = AsyncMock(return_value=True)
    mock_client.delete = AsyncMock(return_value=1) # Returns number of keys deleted
    mock_client.hset = AsyncMock(return_value=1)
    mock_client.hget = AsyncMock(return_value=None)
    mock_client.hdel = AsyncMock(return_value=1)
    mock_client.hgetall = AsyncMock(return_value={})
    mock_client.exists = AsyncMock(return_value=0)
    mock_client.ping = AsyncMock(return_value=True)
    # Ensure the client itself can be awaited (for connection check)
    mock_client.__aenter__.return_value = mock_client
    
    # Patch the redis.asyncio.from_url method to return our mock client
    mocker.patch('redis.asyncio.from_url', return_value=mock_client)
    return mock_client

@pytest.fixture
def sample_conversation_reference():
    """Creates a sample ConversationReference object for testing."""
    return ConversationReference(
        activity_id="act123",
        user=ChannelAccount(id="user1", name="User One", role="user"),
        bot=ChannelAccount(id="bot1", name="Bot", role="bot"),
        conversation=ConversationAccount(id="conv123", name="", is_group=False, conversation_type="personal", tenant_id="tenant1"),
        channel_id="msteams",
        service_url="https://service.url/",
    )

@pytest_asyncio.fixture
async def redis_memory_instance(mock_redis_client):
    """Fixture to get an instance of RedisMemory with a mocked client."""
    # The RedisMemory init will call redis.asyncio.from_url, which is patched
    memory = RedisMemory(redis_url="redis://mock:6379")
    # Since __aenter__ is mocked, we simulate the connection
    await memory._connect()
    # Directly set the connected client for simplicity in tests
    memory.client = mock_redis_client
    return memory

# --- Test Cases ---

@pytest.mark.asyncio
async def test_placeholder_redis():
    assert True

# --- Conversation Reference Tests ---

@pytest.mark.asyncio
async def test_store_conversation_reference_success(redis_memory_instance, mock_redis_client, sample_conversation_reference):
    """Test successfully storing a conversation reference."""
    conv_ref = sample_conversation_reference
    conv_id = conv_ref.conversation.id
    expected_key = f"conv_ref:{conv_id}"
    # Manually serialize to check the stored value
    expected_value = json.dumps(ConversationReference.to_dict(conv_ref))

    await redis_memory_instance.store_conversation_reference(conv_ref)

    mock_redis_client.set.assert_awaited_once_with(expected_key, expected_value, ex=redis_memory_instance.DEFAULT_CONV_REF_TTL)

@pytest.mark.asyncio
async def test_store_conversation_reference_redis_error(redis_memory_instance, mock_redis_client, sample_conversation_reference, mocker):
    """Test store conversation reference handling RedisError."""
    conv_ref = sample_conversation_reference
    mock_redis_client.set.side_effect = RedisError("Failed to set")
    mocker.patch('memory.redis_memory.retry_redis', lambda **kwargs: lambda f: f) # Disable retry for direct error check

    with pytest.raises(RedisError):
        await redis_memory_instance.store_conversation_reference(conv_ref)

@pytest.mark.asyncio
async def test_get_conversation_reference_success(redis_memory_instance, mock_redis_client, sample_conversation_reference):
    """Test successfully retrieving an existing conversation reference."""
    conv_id = sample_conversation_reference.conversation.id
    expected_key = f"conv_ref:{conv_id}"
    stored_value = json.dumps(ConversationReference.to_dict(sample_conversation_reference))
    mock_redis_client.get.return_value = stored_value.encode('utf-8') # Redis client returns bytes

    retrieved_ref = await redis_memory_instance.get_conversation_reference(conv_id)

    mock_redis_client.get.assert_awaited_once_with(expected_key)
    assert retrieved_ref is not None
    # Compare dicts for equality as object comparison might fail
    assert ConversationReference.to_dict(retrieved_ref) == ConversationReference.to_dict(sample_conversation_reference)

@pytest.mark.asyncio
async def test_get_conversation_reference_not_found(redis_memory_instance, mock_redis_client):
    """Test retrieving a non-existent conversation reference."""
    conv_id = "non_existent_conv"
    expected_key = f"conv_ref:{conv_id}"
    mock_redis_client.get.return_value = None # Simulate key not found

    retrieved_ref = await redis_memory_instance.get_conversation_reference(conv_id)

    mock_redis_client.get.assert_awaited_once_with(expected_key)
    assert retrieved_ref is None

@pytest.mark.asyncio
async def test_get_conversation_reference_invalid_json(redis_memory_instance, mock_redis_client):
    """Test retrieving data that is not valid JSON."""
    conv_id = "invalid_json_conv"
    expected_key = f"conv_ref:{conv_id}"
    mock_redis_client.get.return_value = b"this is not json"

    # Should log an error and return None
    retrieved_ref = await redis_memory_instance.get_conversation_reference(conv_id)
    assert retrieved_ref is None
    mock_redis_client.get.assert_awaited_once_with(expected_key)
    # TODO: Add check for logger.error call if logging is critical to test

@pytest.mark.asyncio
async def test_get_conversation_reference_redis_error(redis_memory_instance, mock_redis_client, mocker):
    """Test get conversation reference handling RedisError."""
    conv_id = "error_conv"
    mock_redis_client.get.side_effect = RedisError("Failed to get")
    mocker.patch('memory.redis_memory.retry_redis', lambda **kwargs: lambda f: f)

    with pytest.raises(RedisError):
        await redis_memory_instance.get_conversation_reference(conv_id)

@pytest.mark.asyncio
async def test_delete_conversation_reference_success(redis_memory_instance, mock_redis_client):
    """Test successfully deleting a conversation reference."""
    conv_id = "conv_to_delete"
    expected_key = f"conv_ref:{conv_id}"
    mock_redis_client.delete.return_value = 1 # Simulate successful deletion

    await redis_memory_instance.delete_conversation_reference(conv_id)

    mock_redis_client.delete.assert_awaited_once_with(expected_key)

@pytest.mark.asyncio
async def test_delete_conversation_reference_not_found(redis_memory_instance, mock_redis_client):
    """Test deleting a conversation reference that doesn't exist."""
    conv_id = "conv_not_there"
    expected_key = f"conv_ref:{conv_id}"
    mock_redis_client.delete.return_value = 0 # Simulate key not found

    # Should still complete without error
    await redis_memory_instance.delete_conversation_reference(conv_id)

    mock_redis_client.delete.assert_awaited_once_with(expected_key)

@pytest.mark.asyncio
async def test_delete_conversation_reference_redis_error(redis_memory_instance, mock_redis_client, mocker):
    """Test delete conversation reference handling RedisError."""
    conv_id = "delete_error_conv"
    mock_redis_client.delete.side_effect = RedisError("Failed to delete")
    mocker.patch('memory.redis_memory.retry_redis', lambda **kwargs: lambda f: f)

    with pytest.raises(RedisError):
        await redis_memory_instance.delete_conversation_reference(conv_id)

# --- User Data Tests (To be added) ---

@pytest.mark.asyncio
async def test_store_user_value_success(redis_memory_instance, mock_redis_client):
    """Test storing a single value for a user."""
    user_id = "user123"
    key = "preference_theme"
    value = "dark"
    expected_redis_key = f"user_data:{user_id}"
    expected_redis_value = json.dumps(value)

    await redis_memory_instance.store_user_value(user_id, key, value)

    mock_redis_client.hset.assert_awaited_once_with(expected_redis_key, key, expected_redis_value)

@pytest.mark.asyncio
async def test_store_user_data_success(redis_memory_instance, mock_redis_client):
    """Test storing multiple data points for a user."""
    user_id = "user456"
    data = {"last_repo": "org/repo", "theme": "light", "notify": True}
    expected_redis_key = f"user_data:{user_id}"
    expected_redis_mapping = {k: json.dumps(v) for k, v in data.items()}

    await redis_memory_instance.store_user_data(user_id, data)

    # Check hset was called for each item
    # We expect mapping to be passed directly to hset in the implementation
    mock_redis_client.hset.assert_awaited_once_with(expected_redis_key, mapping=expected_redis_mapping)

@pytest.mark.asyncio
async def test_get_user_value_success(redis_memory_instance, mock_redis_client):
    """Test retrieving a single stored value for a user."""
    user_id = "user123"
    key = "preference_theme"
    stored_value = "dark"
    expected_redis_key = f"user_data:{user_id}"
    mock_redis_client.hget.return_value = json.dumps(stored_value).encode('utf-8')

    value = await redis_memory_instance.get_user_value(user_id, key)

    mock_redis_client.hget.assert_awaited_once_with(expected_redis_key, key)
    assert value == stored_value

@pytest.mark.asyncio
async def test_get_user_value_not_found(redis_memory_instance, mock_redis_client):
    """Test retrieving a non-existent value for a user, returning default."""
    user_id = "user123"
    key = "non_existent_key"
    default_value = "default"
    expected_redis_key = f"user_data:{user_id}"
    mock_redis_client.hget.return_value = None

    value = await redis_memory_instance.get_user_value(user_id, key, default_value)

    mock_redis_client.hget.assert_awaited_once_with(expected_redis_key, key)
    assert value == default_value

@pytest.mark.asyncio
async def test_get_user_value_invalid_json(redis_memory_instance, mock_redis_client):
    """Test retrieving a value that is invalid JSON, returning default."""
    user_id = "user123"
    key = "invalid_json_key"
    default_value = None
    expected_redis_key = f"user_data:{user_id}"
    mock_redis_client.hget.return_value = b"not json"

    value = await redis_memory_instance.get_user_value(user_id, key, default_value)

    mock_redis_client.hget.assert_awaited_once_with(expected_redis_key, key)
    assert value == default_value
    # TODO: Check logger.error call

@pytest.mark.asyncio
async def test_get_user_data_success(redis_memory_instance, mock_redis_client):
    """Test retrieving all data for a user."""
    user_id = "user456"
    expected_redis_key = f"user_data:{user_id}"
    stored_data = {
        b'last_repo': json.dumps("org/repo").encode('utf-8'),
        b'theme': json.dumps("light").encode('utf-8'),
        b'notify': json.dumps(True).encode('utf-8'),
    }
    expected_data = {
        "last_repo": "org/repo",
        "theme": "light",
        "notify": True,
    }
    mock_redis_client.hgetall.return_value = stored_data

    data = await redis_memory_instance.get_user_data(user_id)

    mock_redis_client.hgetall.assert_awaited_once_with(expected_redis_key)
    assert data == expected_data

@pytest.mark.asyncio
async def test_get_user_data_not_found(redis_memory_instance, mock_redis_client):
    """Test retrieving data for a user with no stored data."""
    user_id = "user_no_data"
    expected_redis_key = f"user_data:{user_id}"
    mock_redis_client.hgetall.return_value = {}

    data = await redis_memory_instance.get_user_data(user_id)

    mock_redis_client.hgetall.assert_awaited_once_with(expected_redis_key)
    assert data == {}

@pytest.mark.asyncio
async def test_delete_user_data_success(redis_memory_instance, mock_redis_client):
    """Test deleting a specific key for a user."""
    user_id = "user789"
    key_to_delete = "old_preference"
    expected_redis_key = f"user_data:{user_id}"
    mock_redis_client.hdel.return_value = 1 # Simulate key was deleted

    await redis_memory_instance.delete_user_data(user_id, key_to_delete)

    mock_redis_client.hdel.assert_awaited_once_with(expected_redis_key, key_to_delete)

@pytest.mark.asyncio
async def test_delete_user_data_all_success(redis_memory_instance, mock_redis_client):
    """Test deleting all data for a user."""
    user_id = "user_to_clear"
    expected_redis_key = f"user_data:{user_id}"
    mock_redis_client.delete.return_value = 1 # Simulate key deleted

    await redis_memory_instance.delete_user_data(user_id)

    # Check that the specific hash key wasn't deleted, but the whole user key was
    mock_redis_client.hdel.assert_not_awaited()
    mock_redis_client.delete.assert_awaited_once_with(expected_redis_key)

@pytest.mark.asyncio
async def test_user_data_redis_error(redis_memory_instance, mock_redis_client, mocker):
    """Test user data methods handling RedisError."""
    user_id = "user_error"
    key = "some_key"
    mocker.patch('memory.redis_memory.retry_redis', lambda **kwargs: lambda f: f)

    # Test store_user_value error
    mock_redis_client.hset.side_effect = RedisError("hset failed")
    with pytest.raises(RedisError):
        await redis_memory_instance.store_user_value(user_id, key, "value")
    mock_redis_client.hset.reset_mock() # Reset for next test

    # Test get_user_value error
    mock_redis_client.hget.side_effect = RedisError("hget failed")
    with pytest.raises(RedisError):
        await redis_memory_instance.get_user_value(user_id, key)
    mock_redis_client.hget.reset_mock()

    # Test get_user_data error
    mock_redis_client.hgetall.side_effect = RedisError("hgetall failed")
    with pytest.raises(RedisError):
        await redis_memory_instance.get_user_data(user_id)
    mock_redis_client.hgetall.reset_mock()

    # Test delete_user_data (single key) error
    mock_redis_client.hdel.side_effect = RedisError("hdel failed")
    with pytest.raises(RedisError):
        await redis_memory_instance.delete_user_data(user_id, key)
    mock_redis_client.hdel.reset_mock()

    # Test delete_user_data (all keys) error
    mock_redis_client.delete.side_effect = RedisError("delete failed")
    with pytest.raises(RedisError):
        await redis_memory_instance.delete_user_data(user_id)
    mock_redis_client.delete.reset_mock()

# --- Health Check Test ---

@pytest.mark.asyncio
async def test_health_check_success(redis_memory_instance, mock_redis_client):
    """Test health check when Redis connection is okay."""
    mock_redis_client.ping.return_value = True
    is_healthy, status = await redis_memory_instance.health_check()
    assert is_healthy is True
    assert status == "Connected"
    mock_redis_client.ping.assert_awaited_once()

@pytest.mark.asyncio
async def test_health_check_failure(redis_memory_instance, mock_redis_client):
    """Test health check when Redis ping fails."""
    mock_redis_client.ping.side_effect = RedisError("Connection failed")
    is_healthy, status = await redis_memory_instance.health_check()
    assert is_healthy is False
    assert "Connection failed" in status
    mock_redis_client.ping.assert_awaited_once()

@pytest.mark.asyncio
async def test_health_check_not_connected(redis_memory_instance, mock_redis_client):
    """Test health check when client is None (initial connection failed)."""
    redis_memory_instance.client = None # Simulate failed initial connection
    is_healthy, status = await redis_memory_instance.health_check()
    assert is_healthy is False
    assert status == "Not connected"
    mock_redis_client.ping.assert_not_awaited() # Ping shouldn't be called if not connected 