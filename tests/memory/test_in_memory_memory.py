import pytest
import pytest_asyncio
import json

from botbuilder.schema import ConversationReference, ChannelAccount, ConversationAccount

# Module to test
from memory.in_memory import InMemoryMemory

# --- Fixtures ---

@pytest.fixture
def sample_conversation_reference():
    """Creates a sample ConversationReference object for testing."""
    return ConversationReference(
        activity_id="act456",
        user=ChannelAccount(id="user2", name="User Two", role="user"),
        bot=ChannelAccount(id="bot2", name="Bot", role="bot"),
        conversation=ConversationAccount(id="conv456", name="", is_group=True, conversation_type="channel", tenant_id="tenant2"),
        channel_id="msteams",
        service_url="https://service.url/v2/",
    )

@pytest_asyncio.fixture
async def in_memory_instance():
    """Fixture to get a clean instance of InMemoryMemory for each test."""
    memory = InMemoryMemory()
    # Ensure stores are initialized (though they are in __init__)
    memory.conv_references = {}
    memory.user_data = {}
    # Call connect/disconnect for completeness, although they do nothing
    await memory._connect()
    yield memory # Provide the instance to the test
    await memory._disconnect()

# --- Test Cases ---

@pytest.mark.asyncio
async def test_placeholder_in_memory():
    assert True

# --- Conversation Reference Tests ---

@pytest.mark.asyncio
async def test_store_conversation_reference(in_memory_instance, sample_conversation_reference):
    """Test storing a conversation reference."""
    conv_ref = sample_conversation_reference
    conv_id = conv_ref.conversation.id

    await in_memory_instance.store_conversation_reference(conv_ref)

    assert conv_id in in_memory_instance.conv_references
    # Store a copy, so check dict equality
    assert ConversationReference.to_dict(in_memory_instance.conv_references[conv_id]) == ConversationReference.to_dict(conv_ref)

@pytest.mark.asyncio
async def test_get_conversation_reference_success(in_memory_instance, sample_conversation_reference):
    """Test retrieving an existing conversation reference."""
    conv_ref = sample_conversation_reference
    conv_id = conv_ref.conversation.id
    in_memory_instance.conv_references[conv_id] = conv_ref # Pre-populate

    retrieved_ref = await in_memory_instance.get_conversation_reference(conv_id)

    assert retrieved_ref is not None
    # Return a copy, so check dict equality
    assert ConversationReference.to_dict(retrieved_ref) == ConversationReference.to_dict(conv_ref)

@pytest.mark.asyncio
async def test_get_conversation_reference_not_found(in_memory_instance):
    """Test retrieving a non-existent conversation reference."""
    conv_id = "non_existent_conv"

    retrieved_ref = await in_memory_instance.get_conversation_reference(conv_id)

    assert retrieved_ref is None

@pytest.mark.asyncio
async def test_delete_conversation_reference_success(in_memory_instance, sample_conversation_reference):
    """Test deleting an existing conversation reference."""
    conv_ref = sample_conversation_reference
    conv_id = conv_ref.conversation.id
    in_memory_instance.conv_references[conv_id] = conv_ref # Pre-populate

    await in_memory_instance.delete_conversation_reference(conv_id)

    assert conv_id not in in_memory_instance.conv_references

@pytest.mark.asyncio
async def test_delete_conversation_reference_not_found(in_memory_instance):
    """Test deleting a non-existent conversation reference."""
    conv_id = "non_existent_conv"

    # Should complete without error
    await in_memory_instance.delete_conversation_reference(conv_id)
    assert conv_id not in in_memory_instance.conv_references


# --- User Data Tests ---

@pytest.mark.asyncio
async def test_store_user_value(in_memory_instance):
    """Test storing a single value for a user."""
    user_id = "user1"
    key = "theme"
    value = "dark"

    await in_memory_instance.store_user_value(user_id, key, value)

    assert user_id in in_memory_instance.user_data
    assert key in in_memory_instance.user_data[user_id]
    assert in_memory_instance.user_data[user_id][key] == value

@pytest.mark.asyncio
async def test_store_user_data(in_memory_instance):
    """Test storing multiple data points for a user."""
    user_id = "user2"
    data = {"last_repo": "org/repo", "notify": False, "level": 5}

    await in_memory_instance.store_user_data(user_id, data)

    assert user_id in in_memory_instance.user_data
    # Check that the dictionary was merged/updated correctly
    assert in_memory_instance.user_data[user_id]["last_repo"] == "org/repo"
    assert in_memory_instance.user_data[user_id]["notify"] is False
    assert in_memory_instance.user_data[user_id]["level"] == 5

@pytest.mark.asyncio
async def test_get_user_value_success(in_memory_instance):
    """Test retrieving a single stored value for a user."""
    user_id = "user1"
    key = "theme"
    value = "dark"
    in_memory_instance.user_data[user_id] = {key: value} # Pre-populate

    retrieved_value = await in_memory_instance.get_user_value(user_id, key)

    assert retrieved_value == value

@pytest.mark.asyncio
async def test_get_user_value_not_found(in_memory_instance):
    """Test retrieving a non-existent value, returning default."""
    user_id = "user1"
    key = "non_existent"
    default_value = "default"

    # Test case 1: User exists, key doesn't
    in_memory_instance.user_data[user_id] = {"other_key": "other_value"}
    retrieved_value_1 = await in_memory_instance.get_user_value(user_id, key, default_value)
    assert retrieved_value_1 == default_value

    # Test case 2: User doesn't exist
    retrieved_value_2 = await in_memory_instance.get_user_value("non_existent_user", key, default_value)
    assert retrieved_value_2 == default_value

@pytest.mark.asyncio
async def test_get_user_data_success(in_memory_instance):
    """Test retrieving all data for a user."""
    user_id = "user2"
    data = {"last_repo": "org/repo", "notify": False}
    in_memory_instance.user_data[user_id] = data.copy() # Pre-populate with a copy

    retrieved_data = await in_memory_instance.get_user_data(user_id)

    assert retrieved_data == data
    assert retrieved_data is not data # Ensure it returns a copy

@pytest.mark.asyncio
async def test_get_user_data_not_found(in_memory_instance):
    """Test retrieving data for a user with no stored data."""
    user_id = "user_no_data"

    retrieved_data = await in_memory_instance.get_user_data(user_id)

    assert retrieved_data == {}

@pytest.mark.asyncio
async def test_delete_user_data_single_key(in_memory_instance):
    """Test deleting a specific key for a user."""
    user_id = "user3"
    key_to_delete = "old_pref"
    data = {"other_key": "value", key_to_delete: "delete_me"}
    in_memory_instance.user_data[user_id] = data.copy()

    await in_memory_instance.delete_user_data(user_id, key_to_delete)

    assert user_id in in_memory_instance.user_data
    assert key_to_delete not in in_memory_instance.user_data[user_id]
    assert "other_key" in in_memory_instance.user_data[user_id]

@pytest.mark.asyncio
async def test_delete_user_data_all_keys(in_memory_instance):
    """Test deleting all data for a user."""
    user_id = "user_to_clear"
    data = {"key1": "val1", "key2": "val2"}
    in_memory_instance.user_data[user_id] = data.copy()

    await in_memory_instance.delete_user_data(user_id)

    assert user_id not in in_memory_instance.user_data

@pytest.mark.asyncio
async def test_delete_user_data_user_not_found(in_memory_instance):
    """Test deleting data for a user that doesn't exist."""
    user_id = "non_existent_user"

    # Should complete without error
    await in_memory_instance.delete_user_data(user_id)
    await in_memory_instance.delete_user_data(user_id, "some_key")

    assert user_id not in in_memory_instance.user_data

# --- Health Check Test ---

@pytest.mark.asyncio
async def test_health_check(in_memory_instance):
    """Test health check for InMemoryMemory (always healthy)."""
    is_healthy, status = await in_memory_instance.health_check()
    assert is_healthy is True
    assert status == "Operational" 