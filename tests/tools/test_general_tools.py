import pytest
import pytest_asyncio
import json # For checking user data
from datetime import datetime, timedelta, timezone
from pytest_mock import MockerFixture
from botbuilder.core import TurnContext
from botbuilder.schema import Activity, ConversationAccount, ChannelAccount, ConversationReference
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.memory.memory_base import MemoryBase, MemoryError
from app.tools.general_tools import (
    manage_preferences,
    manage_aliases,
    schedule_reminder,
    suggest_next_actions,
    explain_item,
    ManagePreferencesParams,
    ManageAliasesParams,
    ScheduleReminderParams,
    SuggestNextActionsParams,
    ExplainItemParams,
    USER_DATA_KEY_PREFERENCES, # Assuming this constant exists
    USER_DATA_KEY_ALIASES      # Assuming this constant exists
)
# Assume dateparser is used
import dateparser
# Assume scheduler is accessible (e.g., globally or via context/app state)
# Let's assume it's globally accessible for simplicity in tests
# from app.scheduler import scheduler # If it exists
# Assume proactive helper exists
from app.utils.proactive import send_proactive_message

# --- Fixtures ---

@pytest.fixture
def mock_settings() -> Settings:
    """Mocks application settings."""
    return Settings(
        APP_ID="test_app_id",
        APP_PASSWORD="test_password",
        TEAMS_TENANT_ID="test_tenant_id",
        AZURE_OPENAI_ENDPOINT="test_openai_endpoint",
        AZURE_OPENAI_API_KEY="test_openai_key",
        AZURE_OPENAI_DEPLOYMENT_NAME="test_deployment",
        TOOL_ERROR_CARD_TITLE="Tool Error",
        # Add other settings as needed
    )

@pytest.fixture
def mock_context(mocker: MockerFixture) -> TurnContext:
    """Mocks TurnContext."""
    mock_activity = Activity(
        type="message",
        channel_id="msteams",
        conversation=ConversationAccount(id="conv_id"),
        recipient=ChannelAccount(id="bot_id"),
        from_property=ChannelAccount(id="user_id", name="Test User", aad_object_id="test_aad_id"), # Need AAD ID for user data
        text="manage preferences",
        channel_data={"tenant": {"id": "test_tenant_id"}},
        locale="en-US",
        service_url="https://smba.trafficmanager.net/amer/"
    )
    mock_adapter = mocker.AsyncMock()
    mock_conv_ref = mocker.MagicMock()
    mock_conv_ref.conversation.id = "conv_id"
    mock_adapter.get_conversation_reference.return_value = mock_conv_ref
    mock_context = TurnContext(mock_adapter, mock_activity)
    mocker.patch.object(mock_context, "send_activity", autospec=True)
    return mock_context

@pytest.fixture
def mock_context_no_aad(mock_context: TurnContext) -> TurnContext:
    """Returns a mock context fixture with the AAD object ID removed."""
    mock_context.activity.from_property.aad_object_id = None
    return mock_context

@pytest.fixture
def mock_memory(mocker: MockerFixture) -> MemoryBase:
    """Mocks the MemoryBase interface."""
    memory = mocker.AsyncMock(spec=MemoryBase)
    # Setup default return values
    memory.get_conversation_reference.return_value = mocker.MagicMock()
    memory.get_user_data.return_value = None # Default: no existing data
    memory.store_user_data.return_value = None
    memory.delete_user_data.return_value = None
    return memory

@pytest_asyncio.fixture
async def mock_scheduler(mocker: MockerFixture) -> AsyncIOScheduler:
    """Mocks the global APScheduler instance."""
    # Mock the scheduler object itself
    scheduler_instance = mocker.MagicMock(spec=AsyncIOScheduler)
    # Mock the add_job method
    scheduler_instance.add_job = mocker.MagicMock()
    # Patch where the scheduler is accessed in the tool function
    # Adjust the target path based on where `scheduler` is defined/imported
    mocker.patch("app.tools.general_tools.scheduler", scheduler_instance)
    return scheduler_instance

@pytest.fixture
def mock_conversation_reference() -> ConversationReference:
    """Creates a mock ConversationReference."""
    return ConversationReference(
        activity_id="test_activity_id",
        user=ChannelAccount(id="user_id", name="Test User", aad_object_id="test_aad_id"),
        bot=ChannelAccount(id="bot_id", name="Test Bot"),
        conversation=ConversationAccount(id="conv_id", is_group=False, tenant_id="test_tenant_id"),
        channel_id="msteams",
        locale="en-US",
        service_url="https://smba.trafficmanager.net/amer/"
    )

# --- Test Manage Preferences ---

@pytest.mark.asyncio
async def test_manage_preferences_set_new(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests setting a preference when no user data exists."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManagePreferencesParams(action="set", key="theme", value="dark")

    # Ensure get_user_data returns None initially
    mock_memory.get_user_data.return_value = None

    await manage_preferences(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    # Check that store_user_data was called with the correct new data
    expected_data = {USER_DATA_KEY_PREFERENCES: {"theme": "dark"}}
    mock_memory.store_user_data.assert_called_once_with(user_aad_id, expected_data)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "Preference 'theme' set to 'dark'" in args[0]

@pytest.mark.asyncio
async def test_manage_preferences_set_update(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests updating an existing preference."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManagePreferencesParams(action="set", key="notifications", value="off")

    # Simulate existing data
    existing_data = {USER_DATA_KEY_PREFERENCES: {"theme": "dark", "notifications": "on"}}
    mock_memory.get_user_data.return_value = existing_data

    await manage_preferences(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    # Check that store_user_data was called with updated data
    expected_data = {USER_DATA_KEY_PREFERENCES: {"theme": "dark", "notifications": "off"}}
    mock_memory.store_user_data.assert_called_once_with(user_aad_id, expected_data)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "Preference 'notifications' updated to 'off'" in args[0]

@pytest.mark.asyncio
async def test_manage_preferences_view_existing(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests viewing existing preferences."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManagePreferencesParams(action="view")

    # Simulate existing data
    existing_data = {USER_DATA_KEY_PREFERENCES: {"theme": "light", "timezone": "UTC"}, "other_key": "value"}
    mock_memory.get_user_data.return_value = existing_data

    await manage_preferences(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    mock_memory.store_user_data.assert_not_called() # View shouldn't store
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    response_text = args[0]
    assert "Current Preferences:" in response_text
    assert "- theme: light" in response_text
    assert "- timezone: UTC" in response_text
    assert "other_key" not in response_text # Only show preferences

@pytest.mark.asyncio
async def test_manage_preferences_view_none(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests viewing preferences when none are set."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManagePreferencesParams(action="view")

    # No user data or no preferences key
    mock_memory.get_user_data.side_effect = [None, {"other_key": "value"}]

    # Test case 1: No user data at all
    await manage_preferences(mock_context, mock_memory, mock_settings, params)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "You have no preferences set." in args[0]
    mock_context.send_activity.reset_mock()

    # Test case 2: User data exists, but no 'preferences' key
    await manage_preferences(mock_context, mock_memory, mock_settings, params)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "You have no preferences set." in args[0]

    assert mock_memory.get_user_data.call_count == 2

@pytest.mark.asyncio
async def test_manage_preferences_delete_existing(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests deleting an existing preference."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManagePreferencesParams(action="delete", key="timezone")

    existing_data = {USER_DATA_KEY_PREFERENCES: {"theme": "dark", "timezone": "PST"}}
    mock_memory.get_user_data.return_value = existing_data

    await manage_preferences(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    expected_data = {USER_DATA_KEY_PREFERENCES: {"theme": "dark"}} # timezone removed
    mock_memory.store_user_data.assert_called_once_with(user_aad_id, expected_data)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "Preference 'timezone' deleted." in args[0]

@pytest.mark.asyncio
async def test_manage_preferences_delete_nonexistent(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests deleting a preference that doesn't exist."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManagePreferencesParams(action="delete", key="nonexistent")

    existing_data = {USER_DATA_KEY_PREFERENCES: {"theme": "dark"}}
    mock_memory.get_user_data.return_value = existing_data

    await manage_preferences(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    mock_memory.store_user_data.assert_not_called() # No change, so shouldn't store
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "Preference 'nonexistent' not found." in args[0]

@pytest.mark.asyncio
async def test_manage_preferences_no_aad_id(
    mock_context_no_aad: TurnContext, # Use the modified fixture
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests that an error is returned if AAD object ID is missing."""
    params = ManagePreferencesParams(action="set", key="theme", value="dark")

    await manage_preferences(mock_context_no_aad, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_not_called()
    mock_memory.store_user_data.assert_not_called()
    mock_context_no_aad.send_activity.assert_called_once()
    args, _ = mock_context_no_aad.send_activity.call_args
    assert "Could not identify user (missing AAD ID). Cannot manage preferences." in args[0]

@pytest.mark.asyncio
async def test_manage_preferences_memory_error_get(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture
):
    """Tests handling MemoryError during get_user_data."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManagePreferencesParams(action="view")
    mock_memory.get_user_data.side_effect = MemoryError("Failed to connect")
    mocker.patch("app.tools.general_tools.create_error_card_response", return_value=mocker.MagicMock())

    await manage_preferences(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    mock_memory.store_user_data.assert_not_called()
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.general_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Error accessing user data for preferences" in args[1]
    assert "Failed to connect" in args[1]

@pytest.mark.asyncio
async def test_manage_preferences_memory_error_store(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture
):
    """Tests handling MemoryError during store_user_data."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManagePreferencesParams(action="set", key="theme", value="dark")
    mock_memory.get_user_data.return_value = None # Start fresh
    mock_memory.store_user_data.side_effect = MemoryError("Write failed")
    mocker.patch("app.tools.general_tools.create_error_card_response", return_value=mocker.MagicMock())

    await manage_preferences(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    mock_memory.store_user_data.assert_called_once() # It was called
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.general_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Error saving user preferences" in args[1]
    assert "Write failed" in args[1]

# --- Test Manage Aliases ---

@pytest.mark.asyncio
async def test_manage_aliases_set_new(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests setting a new alias when no user data exists."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManageAliasesParams(action="set", alias_name="prod-deploy", alias_value="deploy --project MyWebApp --env Production")

    mock_memory.get_user_data.return_value = None

    await manage_aliases(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    expected_data = {USER_DATA_KEY_ALIASES: {"prod-deploy": "deploy --project MyWebApp --env Production"}}
    mock_memory.store_user_data.assert_called_once_with(user_aad_id, expected_data)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "Alias 'prod-deploy' set." in args[0]

@pytest.mark.asyncio
async def test_manage_aliases_set_update(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests updating an existing alias."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManageAliasesParams(action="set", alias_name="prod-deploy", alias_value="deploy --project MyWebAppV2 --env Production")

    existing_data = {USER_DATA_KEY_ALIASES: {"prod-deploy": "old value", "other-alias": "abc"}}
    mock_memory.get_user_data.return_value = existing_data

    await manage_aliases(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    expected_data = {USER_DATA_KEY_ALIASES: {"prod-deploy": "deploy --project MyWebAppV2 --env Production", "other-alias": "abc"}}
    mock_memory.store_user_data.assert_called_once_with(user_aad_id, expected_data)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "Alias 'prod-deploy' updated." in args[0]

@pytest.mark.asyncio
async def test_manage_aliases_view_existing(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests viewing existing aliases."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManageAliasesParams(action="view")

    existing_data = {USER_DATA_KEY_ALIASES: {"alias1": "value1", "alias2": "value2"}, "pref_key": {}}
    mock_memory.get_user_data.return_value = existing_data

    await manage_aliases(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    mock_memory.store_user_data.assert_not_called()
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    response_text = args[0]
    assert "Current Aliases:" in response_text
    assert "- alias1: value1" in response_text
    assert "- alias2: value2" in response_text
    assert "pref_key" not in response_text

@pytest.mark.asyncio
async def test_manage_aliases_view_none(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests viewing aliases when none are set."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManageAliasesParams(action="view")

    mock_memory.get_user_data.side_effect = [None, {"other_key": "value"}]

    # Test case 1: No user data
    await manage_aliases(mock_context, mock_memory, mock_settings, params)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "You have no aliases set." in args[0]
    mock_context.send_activity.reset_mock()

    # Test case 2: User data, but no aliases key
    await manage_aliases(mock_context, mock_memory, mock_settings, params)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "You have no aliases set." in args[0]

    assert mock_memory.get_user_data.call_count == 2

@pytest.mark.asyncio
async def test_manage_aliases_delete_existing(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
):
    """Tests deleting an existing alias."""
    user_aad_id = mock_context.activity.from_property.aad_object_id
    params = ManageAliasesParams(action="delete", alias_name="to-delete")

    existing_data = {USER_DATA_KEY_ALIASES: {"keep": "this", "to-delete": "that"}}
    mock_memory.get_user_data.return_value = existing_data

    await manage_aliases(mock_context, mock_memory, mock_settings, params)

    mock_memory.get_user_data.assert_called_once_with(user_aad_id)
    expected_data = {USER_DATA_KEY_ALIASES: {"keep": "this"}}
    mock_memory.store_user_data.assert_called_once_with(user_aad_id, expected_data)
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert "Alias 'to-delete' deleted." in args[0]

# --- Test Schedule Reminder ---

@pytest.mark.asyncio
async def test_schedule_reminder_success(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_scheduler: AsyncIOScheduler,
    mock_conversation_reference: ConversationReference,
    mocker: MockerFixture
):
    """Tests successfully scheduling a reminder."""
    reminder_time_str = "in 5 minutes"
    reminder_text = "Check the build status"
    params = ScheduleReminderParams(time=reminder_time_str, reminder=reminder_text)

    # Mock dateparser.parse to return a specific future time
    run_time = datetime.now(timezone.utc) + timedelta(minutes=5)
    mocker.patch("dateparser.parse", return_value=run_time)

    # Mock memory to return the conversation reference
    mock_memory.get_conversation_reference.return_value = mock_conversation_reference

    # Mock the proactive message helper (just to ensure it's targeted by add_job)
    mock_send_proactive = mocker.patch("app.utils.proactive.send_proactive_message")

    await schedule_reminder(mock_context, mock_memory, mock_settings, params)

    # Check dateparser was called
    mocker.patch("dateparser.parse").assert_called_once_with(reminder_time_str, settings={'PREFER_DATES_FROM': 'future'})

    # Check conversation reference was fetched
    mock_memory.get_conversation_reference.assert_called_once_with(mock_context.activity.conversation.id)

    # Check scheduler.add_job was called correctly
    mock_scheduler.add_job.assert_called_once()
    args, kwargs = mock_scheduler.add_job.call_args
    assert args[0] == send_proactive_message # Check the target function
    assert kwargs["trigger"] == "date"
    assert kwargs["run_date"] == run_time
    job_args = kwargs["args"]
    assert job_args[0] == mock_context.adapter # Adapter
    assert job_args[1] == mock_conversation_reference # Conversation Reference
    assert reminder_text in job_args[2] # Reminder text
    assert job_args[3] == mock_settings.APP_ID # App ID
    assert job_args[4] == mock_settings.APP_PASSWORD # App Password

    # Check confirmation message was sent
    mock_context.send_activity.assert_called_once()
    call_args, _ = mock_context.send_activity.call_args
    response_text = call_args[0]
    assert f"OK. I will remind you to '{reminder_text}'" in response_text
    # We might want to assert the formatted time is mentioned too

@pytest.mark.asyncio
async def test_schedule_reminder_invalid_time(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_scheduler: AsyncIOScheduler,
    mocker: MockerFixture
):
    """Tests failure when dateparser cannot parse the time string."""
    reminder_time_str = "next tuesdayish maybe"
    params = ScheduleReminderParams(time=reminder_time_str, reminder="Do something")

    # Mock dateparser.parse to return None
    mocker.patch("dateparser.parse", return_value=None)

    await schedule_reminder(mock_context, mock_memory, mock_settings, params)

    mocker.patch("dateparser.parse").assert_called_once()
    mock_scheduler.add_job.assert_not_called()
    mock_context.send_activity.assert_called_once()
    call_args, _ = mock_context.send_activity.call_args
    assert f"Sorry, I couldn't understand the time '{reminder_time_str}'." in call_args[0]

@pytest.mark.asyncio
async def test_schedule_reminder_no_conv_ref(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_scheduler: AsyncIOScheduler,
    mocker: MockerFixture
):
    """Tests failure when conversation reference cannot be retrieved."""
    reminder_time_str = "tomorrow 10am"
    params = ScheduleReminderParams(time=reminder_time_str, reminder="Meeting")

    run_time = datetime.now(timezone.utc) + timedelta(days=1)
    mocker.patch("dateparser.parse", return_value=run_time)

    # Mock memory to return None for conv ref
    mock_memory.get_conversation_reference.return_value = None
    mocker.patch("app.tools.general_tools.create_error_card_response", return_value=mocker.MagicMock())

    await schedule_reminder(mock_context, mock_memory, mock_settings, params)

    mocker.patch("dateparser.parse").assert_called_once()
    mock_memory.get_conversation_reference.assert_called_once_with(mock_context.activity.conversation.id)
    mock_scheduler.add_job.assert_not_called()
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.general_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Could not find conversation reference to schedule reminder." in args[1]

@pytest.mark.asyncio
async def test_schedule_reminder_scheduler_error(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_scheduler: AsyncIOScheduler,
    mock_conversation_reference: ConversationReference,
    mocker: MockerFixture
):
    """Tests failure when scheduler.add_job raises an exception."""
    reminder_time_str = "in 1 hour"
    params = ScheduleReminderParams(time=reminder_time_str, reminder="Take break")

    run_time = datetime.now(timezone.utc) + timedelta(hours=1)
    mocker.patch("dateparser.parse", return_value=run_time)
    mock_memory.get_conversation_reference.return_value = mock_conversation_reference

    # Mock add_job to raise an error
    mock_scheduler.add_job.side_effect = Exception("Scheduler database locked")
    mocker.patch("app.tools.general_tools.create_error_card_response", return_value=mocker.MagicMock())

    await schedule_reminder(mock_context, mock_memory, mock_settings, params)

    mocker.patch("dateparser.parse").assert_called_once()
    mock_memory.get_conversation_reference.assert_called_once()
    mock_scheduler.add_job.assert_called_once() # It was called
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.general_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Could not schedule reminder due to scheduler error" in args[1]
    assert "Scheduler database locked" in args[1]

# --- Test Suggest Next Actions ---

@pytest.mark.asyncio
async def test_suggest_next_actions_success(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture
):
    """Tests successfully suggesting next actions."""
    # Optional: Explicitly provide context if needed by the tool
    params = SuggestNextActionsParams(context="User asked about deployments")

    # Mock LLM call
    expected_suggestions = "1. Trigger a deployment\n2. Check deployment status\n3. View project settings"
    mock_llm_call = mocker.patch("app.llm.get_completion", return_value=expected_suggestions)
    mocker.patch("app.tools.general_tools.create_adaptive_card_response", return_value=mocker.MagicMock()) # Mock card response

    await suggest_next_actions(mock_context, mock_memory, mock_settings, params)

    # Check LLM was called (potentially check prompt content)
    mock_llm_call.assert_called_once()
    call_args, _ = mock_llm_call.call_args
    prompt = call_args[0]
    assert "Suggest next logical actions" in prompt
    if params.context:
        assert params.context in prompt
    else: # Check if text from activity is used if no context param
        assert mock_context.activity.text in prompt

    # Check response was sent
    mock_context.send_activity.assert_called_once()
    card_mock = mocker.patch("app.tools.general_tools.create_adaptive_card_response")
    card_mock.assert_called_once()
    # Optionally check card content for suggestions
    # args, kwargs = card_mock.call_args
    # card_payload = kwargs.get('card_payload', {})
    # assert expected_suggestions in json.dumps(card_payload)

@pytest.mark.asyncio
async def test_suggest_next_actions_llm_error(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture
):
    """Tests handling errors during LLM call for suggestions."""
    params = SuggestNextActionsParams(context="Error context")

    # Mock LLM call to raise an exception
    mock_llm_call = mocker.patch("app.llm.get_completion", side_effect=Exception("Azure OpenAI Error"))
    mocker.patch("app.tools.general_tools.create_error_card_response", return_value=mocker.MagicMock())

    await suggest_next_actions(mock_context, mock_memory, mock_settings, params)

    mock_llm_call.assert_called_once()
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.general_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Error generating suggestions" in args[1]
    assert "Azure OpenAI Error" in args[1]


# --- Test Explain Item ---

@pytest.mark.asyncio
async def test_explain_item_success(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture
):
    """Tests successfully explaining an item."""
    item_to_explain = "RBAC"
    params = ExplainItemParams(item_to_explain=item_to_explain)

    # Mock LLM call
    expected_explanation = "RBAC stands for Role-Based Access Control..."
    mock_llm_call = mocker.patch("app.llm.get_completion", return_value=expected_explanation)

    await explain_item(mock_context, mock_memory, mock_settings, params)

    # Check LLM was called
    mock_llm_call.assert_called_once()
    call_args, _ = mock_llm_call.call_args
    prompt = call_args[0]
    assert f"Explain the following item: {item_to_explain}" in prompt

    # Check response was sent
    mock_context.send_activity.assert_called_once()
    call_args, _ = mock_context.send_activity.call_args
    assert expected_explanation in call_args[0]

@pytest.mark.asyncio
async def test_explain_item_llm_error(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture
):
    """Tests handling errors during LLM call for explanation."""
    item_to_explain = "quantum entanglement"
    params = ExplainItemParams(item_to_explain=item_to_explain)

    # Mock LLM call to raise an exception
    mock_llm_call = mocker.patch("app.llm.get_completion", side_effect=Exception("Model connection failed"))
    mocker.patch("app.tools.general_tools.create_error_card_response", return_value=mocker.MagicMock())

    await explain_item(mock_context, mock_memory, mock_settings, params)

    mock_llm_call.assert_called_once()
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.general_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert f"Error explaining item '{item_to_explain}'" in args[1]
    assert "Model connection failed" in args[1]


# --- Add tests for send_user_digest if needed --- 