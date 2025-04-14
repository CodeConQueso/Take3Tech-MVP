import pytest
import pytest_asyncio
from pytest_mock import MockerFixture
from botbuilder.core import TurnContext, MessageFactory
from botbuilder.schema import Activity, ConversationAccount, ChannelAccount
from jira import JIRA, Issue, JIRAError

from app.config import Settings
from app.memory.memory_base import MemoryBase
from app.tools.jira_tools import (
    jira_create_issue,
    jira_update_status,
    jira_summarize_feedback,
    JiraCreateIssueParams,
    JiraUpdateStatusParams,
    JiraSummarizeFeedbackParams,
)
# Assume the client getter might raise an exception or return None
from app.utils.api_clients import get_jira_client, JiraClientError


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
        # Add other relevant settings, especially Jira related if any
        JIRA_SERVER="https://test.atlassian.net",
        JIRA_USERNAME="test_user",
        JIRA_API_TOKEN="test_token",
        JIRA_DEFAULT_PROJECT="TESTPROJ",
        JIRA_DEFAULT_ISSUE_TYPE="Task",
        TOOL_ERROR_CARD_TITLE="Tool Error",
    )

@pytest.fixture
def mock_context(mocker: MockerFixture) -> TurnContext:
    """Mocks TurnContext and related objects."""
    mock_activity = Activity(
        type="message",
        channel_id="msteams",
        conversation=ConversationAccount(id="conv_id"),
        recipient=ChannelAccount(id="bot_id"),
        from_property=ChannelAccount(id="user_id", name="Test User"),
        text="do something with jira",
        channel_data={"tenant": {"id": "test_tenant_id"}},
        locale="en-US",
        service_url="https://smba.trafficmanager.net/amer/" # Example service URL
    )
    mock_adapter = mocker.AsyncMock()
    # Ensure get_conversation_reference returns a usable object
    mock_conv_ref = mocker.MagicMock()
    mock_conv_ref.conversation.id = "conv_id"
    mock_adapter.get_conversation_reference.return_value = mock_conv_ref
    mock_context = TurnContext(mock_adapter, mock_activity)
    mocker.patch.object(mock_context, "send_activity", autospec=True) # Mock send_activity
    return mock_context

@pytest.fixture
def mock_memory(mocker: MockerFixture) -> MemoryBase:
    """Mocks the MemoryBase interface."""
    memory = mocker.AsyncMock(spec=MemoryBase)
    memory.get_conversation_reference.return_value = mocker.MagicMock() # Simplified
    memory.get_user_data.return_value = {} # Default empty user data
    return memory

@pytest_asyncio.fixture
async def mock_jira_client(mocker: MockerFixture, mock_settings: Settings) -> JIRA:
    """Mocks the JIRA client."""
    # Mock the client object itself
    mock_client = mocker.MagicMock(spec=JIRA)
    mock_issue = mocker.MagicMock(spec=Issue, key="TESTPROJ-123")
    mock_issue.permalink.return_value = f"{mock_settings.JIRA_SERVER}/browse/TESTPROJ-123" # Mock permalink

    # Mock specific methods we'll use
    mock_client.create_issue = mocker.MagicMock(return_value=mock_issue)
    mock_client.transition_issue = mocker.MagicMock()
    mock_client.issue = mocker.MagicMock(return_value=mocker.MagicMock(spec=Issue, key="TESTPROJ-456"))
    mock_client.transitions = mocker.MagicMock(return_value=[{'id': '5', 'name': 'Done'}]) # Example transitions

    # Patch the getter function to return our mock client
    mocker.patch("app.utils.api_clients.get_jira_client", return_value=mock_client)
    return mock_client


# --- Test Jira Create Issue ---

@pytest.mark.asyncio
async def test_jira_create_issue_success_defaults(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests successful Jira issue creation using default project and issue type."""
    params = JiraCreateIssueParams(summary="Test Summary", description="Test Description")
    mocker.patch("app.tools.jira_tools.create_adaptive_card_response", return_value=mocker.MagicMock()) # Mock card response

    await jira_create_issue(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.create_issue.assert_called_once_with(
        project=mock_settings.JIRA_DEFAULT_PROJECT,
        summary="Test Summary",
        description="Test Description",
        issuetype={'name': mock_settings.JIRA_DEFAULT_ISSUE_TYPE}
    )
    # Check if send_activity was called (indirectly via create_adaptive_card_response)
    mock_context.send_activity.assert_called_once()
    # We could inspect the arguments of send_activity more deeply if needed


@pytest.mark.asyncio
async def test_jira_create_issue_success_specified(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests successful Jira issue creation specifying project and issue type."""
    params = JiraCreateIssueParams(
        project_key="OTHERPROJ",
        issue_type="Bug",
        summary="Test Bug Summary",
        description="Test Bug Description"
    )
    mocker.patch("app.tools.jira_tools.create_adaptive_card_response", return_value=mocker.MagicMock()) # Mock card response

    await jira_create_issue(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.create_issue.assert_called_once_with(
        project="OTHERPROJ",
        summary="Test Bug Summary",
        description="Test Bug Description",
        issuetype={'name': "Bug"}
    )
    mock_context.send_activity.assert_called_once()

@pytest.mark.asyncio
async def test_jira_create_issue_api_error(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests handling of JIRAError during issue creation."""
    params = JiraCreateIssueParams(summary="Test Summary", description="Test Description")
    mock_jira_client.create_issue.side_effect = JIRAError(text="Creation Failed", status_code=500)
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock()) # Mock error card

    await jira_create_issue(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.create_issue.assert_called_once() # Still called
    mock_context.send_activity.assert_called_once()
    # Check that the error card function was called
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    # Optionally check args passed to error card creator
    args, _ = error_card_mock.call_args
    assert args[0] == mock_context
    assert "Error creating Jira issue" in args[1]
    assert "Creation Failed" in args[1]


@pytest.mark.asyncio
async def test_jira_create_issue_client_unavailable(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture, # Note: No mock_jira_client fixture used here
):
    """Tests handling when the Jira client cannot be obtained."""
    params = JiraCreateIssueParams(summary="Test Summary", description="Test Description")
    # Make the client getter raise an error
    mocker.patch("app.utils.api_clients.get_jira_client", side_effect=JiraClientError("Config error"))
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock())

    await jira_create_issue(mock_context, mock_memory, mock_settings, params)

    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Failed to initialize Jira client" in args[1]
    assert "Config error" in args[1]


# --- Test Jira Update Status ---

@pytest.mark.asyncio
async def test_jira_update_status_success(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests successful Jira issue status update."""
    issue_key = "TESTPROJ-123"
    target_status = "Done"
    params = JiraUpdateStatusParams(issue_key=issue_key, target_status=target_status)

    # Mock the transitions call for this specific test
    mock_jira_client.transitions.return_value = [
        {"id": "1", "name": "To Do"},
        {"id": "5", "name": "Done"}, # Target status
        {"id": "10", "name": "In Progress"},
    ]
    mock_jira_client.transition_issue.return_value = None # Transition call returns None

    await jira_update_status(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.transitions.assert_called_once_with(issue_key)
    mock_jira_client.transition_issue.assert_called_once_with(issue_key, "5") # Correct transition ID
    mock_context.send_activity.assert_called_once()
    # Check the success message content
    args, _ = mock_context.send_activity.call_args
    assert f"Successfully transitioned issue {issue_key} to '{target_status}'" in args[0]


@pytest.mark.asyncio
async def test_jira_update_status_invalid_transition_name(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests handling when the target status name is not found."""
    issue_key = "TESTPROJ-456"
    target_status = "Won't Fix" # Status not in mock transitions
    params = JiraUpdateStatusParams(issue_key=issue_key, target_status=target_status)

    mock_jira_client.transitions.return_value = [
        {"id": "1", "name": "To Do"},
        {"id": "5", "name": "Done"},
    ]
    mock_jira_client.transition_issue.reset_mock() # Ensure it's not called

    await jira_update_status(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.transitions.assert_called_once_with(issue_key)
    mock_jira_client.transition_issue.assert_not_called()
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert f"Invalid target status '{target_status}' for issue {issue_key}." in args[0]
    assert "Available statuses: To Do, Done" in args[0]


@pytest.mark.asyncio
async def test_jira_update_status_api_error_fetching_transitions(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests handling JIRAError when fetching transitions."""
    issue_key = "TESTPROJ-789"
    target_status = "Done"
    params = JiraUpdateStatusParams(issue_key=issue_key, target_status=target_status)

    mock_jira_client.transitions.side_effect = JIRAError(text="Fetch failed", status_code=404)
    mock_jira_client.transition_issue.reset_mock()
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock()) # Mock error card

    await jira_update_status(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.transitions.assert_called_once_with(issue_key)
    mock_jira_client.transition_issue.assert_not_called()
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert f"Error fetching transitions for Jira issue {issue_key}" in args[1]
    assert "Fetch failed" in args[1]


@pytest.mark.asyncio
async def test_jira_update_status_api_error_during_transition(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests handling JIRAError during the transition call."""
    issue_key = "TESTPROJ-101"
    target_status = "Done"
    transition_id = "5"
    params = JiraUpdateStatusParams(issue_key=issue_key, target_status=target_status)

    mock_jira_client.transitions.return_value = [{"id": transition_id, "name": target_status}]
    mock_jira_client.transition_issue.side_effect = JIRAError(text="Transition failed", status_code=400)
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock()) # Mock error card

    await jira_update_status(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.transitions.assert_called_once_with(issue_key)
    mock_jira_client.transition_issue.assert_called_once_with(issue_key, transition_id)
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert f"Error transitioning Jira issue {issue_key} to '{target_status}'" in args[1]
    assert "Transition failed" in args[1]


@pytest.mark.asyncio
async def test_jira_update_status_client_unavailable(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture,
):
    """Tests handling when the Jira client cannot be obtained."""
    params = JiraUpdateStatusParams(issue_key="TESTPROJ-111", target_status="Done")
    mocker.patch("app.utils.api_clients.get_jira_client", side_effect=JiraClientError("Auth error"))
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock())

    await jira_update_status(mock_context, mock_memory, mock_settings, params)

    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Failed to initialize Jira client" in args[1]
    assert "Auth error" in args[1]


# --- Test Jira Summarize Feedback ---

@pytest.mark.asyncio
async def test_jira_summarize_feedback_success(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests successful Jira feedback summarization."""
    issue_key = "TESTPROJ-999"
    params = JiraSummarizeFeedbackParams(issue_key=issue_key)

    # Mock issue fetch
    mock_issue = mocker.MagicMock(spec=Issue, key=issue_key)
    mock_issue.fields = mocker.MagicMock()
    mock_issue.fields.summary = "Original Summary"
    mock_issue.fields.description = "Original Description"
    mock_jira_client.issue.return_value = mock_issue

    # Mock comment fetch
    mock_comment1 = mocker.MagicMock(body="This is the first comment.")
    mock_comment2 = mocker.MagicMock(body="This is the second comment.")
    mock_jira_client.comments.return_value = [mock_comment1, mock_comment2]

    # Mock LLM call
    expected_summary = "Summary of feedback: Comment 1 and 2 discussed..."
    mock_llm_call = mocker.patch("app.llm.get_completion", return_value=expected_summary)
    mocker.patch("app.tools.jira_tools.create_adaptive_card_response", return_value=mocker.MagicMock())

    await jira_summarize_feedback(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.issue.assert_called_once_with(issue_key)
    mock_jira_client.comments.assert_called_once_with(mock_issue)
    # Check that LLM was called with the correct context
    mock_llm_call.assert_called_once()
    call_args, _ = mock_llm_call.call_args
    prompt = call_args[0]
    assert "Original Summary" in prompt
    assert "Original Description" in prompt
    assert "This is the first comment." in prompt
    assert "This is the second comment." in prompt
    assert "Summarize the comments" in prompt

    # Check that a response was sent
    mock_context.send_activity.assert_called_once()
    # We could check the card creator was called with the expected summary if needed


@pytest.mark.asyncio
async def test_jira_summarize_feedback_no_comments(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests summarization when the issue has no comments."""
    issue_key = "TESTPROJ-1000"
    params = JiraSummarizeFeedbackParams(issue_key=issue_key)

    mock_issue = mocker.MagicMock(spec=Issue, key=issue_key)
    mock_issue.fields = mocker.MagicMock()
    mock_issue.fields.summary = "Summary"
    mock_issue.fields.description = "Description"
    mock_jira_client.issue.return_value = mock_issue
    mock_jira_client.comments.return_value = [] # No comments

    mock_llm_call = mocker.patch("app.llm.get_completion") # LLM should not be called

    await jira_summarize_feedback(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.issue.assert_called_once_with(issue_key)
    mock_jira_client.comments.assert_called_once_with(mock_issue)
    mock_llm_call.assert_not_called()
    mock_context.send_activity.assert_called_once()
    args, _ = mock_context.send_activity.call_args
    assert f"Issue {issue_key} has no comments to summarize." in args[0]


@pytest.mark.asyncio
async def test_jira_summarize_feedback_issue_not_found(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests handling when the Jira issue is not found."""
    issue_key = "NOTFOUND-1"
    params = JiraSummarizeFeedbackParams(issue_key=issue_key)

    mock_jira_client.issue.side_effect = JIRAError(text="Issue not found", status_code=404)
    mock_jira_client.comments.reset_mock()
    mocker.patch("app.llm.get_completion")
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock())

    await jira_summarize_feedback(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.issue.assert_called_once_with(issue_key)
    mock_jira_client.comments.assert_not_called()
    mocker.patch("app.llm.get_completion").assert_not_called()
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert f"Error fetching Jira issue {issue_key}" in args[1]
    assert "Issue not found" in args[1]


@pytest.mark.asyncio
async def test_jira_summarize_feedback_comment_fetch_error(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests handling JIRAError when fetching comments."""
    issue_key = "TESTPROJ-1001"
    params = JiraSummarizeFeedbackParams(issue_key=issue_key)

    mock_issue = mocker.MagicMock(spec=Issue, key=issue_key)
    mock_jira_client.issue.return_value = mock_issue
    mock_jira_client.comments.side_effect = JIRAError(text="Cannot get comments", status_code=500)
    mocker.patch("app.llm.get_completion")
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock())

    await jira_summarize_feedback(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.issue.assert_called_once_with(issue_key)
    mock_jira_client.comments.assert_called_once_with(mock_issue)
    mocker.patch("app.llm.get_completion").assert_not_called()
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert f"Error fetching comments for Jira issue {issue_key}" in args[1]
    assert "Cannot get comments" in args[1]


@pytest.mark.asyncio
async def test_jira_summarize_feedback_llm_error(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_jira_client: JIRA,
    mocker: MockerFixture,
):
    """Tests handling an error during the LLM summarization call."""
    issue_key = "TESTPROJ-1002"
    params = JiraSummarizeFeedbackParams(issue_key=issue_key)

    mock_issue = mocker.MagicMock(spec=Issue, key=issue_key)
    mock_issue.fields = mocker.MagicMock()
    mock_issue.fields.summary = "Summary"
    mock_issue.fields.description = "Description"
    mock_jira_client.issue.return_value = mock_issue
    mock_jira_client.comments.return_value = [mocker.MagicMock(body="A comment")]

    mock_llm_call = mocker.patch("app.llm.get_completion", side_effect=Exception("LLM unavailable"))
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock())

    await jira_summarize_feedback(mock_context, mock_memory, mock_settings, params)

    mock_jira_client.issue.assert_called_once_with(issue_key)
    mock_jira_client.comments.assert_called_once_with(mock_issue)
    mock_llm_call.assert_called_once() # LLM was called
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert f"Error summarizing feedback for Jira issue {issue_key}" in args[1]
    assert "LLM unavailable" in args[1]


@pytest.mark.asyncio
async def test_jira_summarize_feedback_client_unavailable(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture,
):
    """Tests handling when the Jira client cannot be obtained."""
    params = JiraSummarizeFeedbackParams(issue_key="TESTPROJ-111")
    mocker.patch("app.utils.api_clients.get_jira_client", side_effect=JiraClientError("Bad config"))
    mocker.patch("app.llm.get_completion")
    mocker.patch("app.tools.jira_tools.create_error_card_response", return_value=mocker.MagicMock())

    await jira_summarize_feedback(mock_context, mock_memory, mock_settings, params)

    mocker.patch("app.llm.get_completion").assert_not_called()
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.jira_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Failed to initialize Jira client" in args[1]
    assert "Bad config" in args[1]


# --- Add more tests for various scenarios (errors, edge cases) --- 