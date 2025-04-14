import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import json
import hashlib
import hmac

from fastapi import FastAPI, BackgroundTasks
from httpx import AsyncClient
from botbuilder.core import BotFrameworkAdapter
from botbuilder.schema import ConversationReference, ChannelAccount, ConversationAccount

# Assuming main.py initializes the app
from main import app as fastapi_app # Import your FastAPI app instance
from memory.interface import MemoryInterface
from utils.config import Settings
import webhook_handlers # To patch functions within
import utils.security # To patch signature verification
import cards # To patch card creation
import utils.helpers # To patch proactive message sending

# --- Sample Payloads ---

@pytest.fixture
def sample_github_payload():
    return {
        "action": "opened",
        "number": 123,
        "pull_request": {
            "url": "https://api.github.com/repos/octocat/Hello-World/pulls/123",
            "html_url": "https://github.com/octocat/Hello-World/pull/123",
            "id": 1,
            "number": 123,
            "state": "open",
            "title": "Amazing new feature",
            "user": {"login": "octocat"},
            "body": "Please pull these awesome changes",
            "created_at": "2011-01-26T19:01:12Z",
            "updated_at": "2011-01-26T19:01:12Z",
            "closed_at": None,
            "merged_at": None,
            "merge_commit_sha": "e5bd3914e2e596debea16f433f57875b5b90bcd6",
            "assignee": None,
            "assignees": [],
            "requested_reviewers": [],
            "requested_teams": [],
            "labels": [],
            "milestone": None,
            "draft": False,
            "commits_url": "",
            "review_comments_url": "",
            "review_comment_url": "",
            "comments_url": "",
            "statuses_url": "",
            "head": {"label": "octocat:new-feature", "ref": "new-feature", "sha": "", "user": {}, "repo": {}},
            "base": {"label": "octocat:main", "ref": "main", "sha": "", "user": {}, "repo": {}},
            "_links": {},
            "author_association": "OWNER",
            "auto_merge": None,
            "active_lock_reason": None,
            "merged": False,
            "mergeable": True,
            "rebaseable": True,
            "mergeable_state": "clean",
            "merged_by": None,
            "comments": 0,
            "review_comments": 0,
            "maintainer_can_modify": False,
            "commits": 1,
            "additions": 10,
            "deletions": 2,
            "changed_files": 1,
        },
        "repository": {
            "id": 1296269,
            "name": "Hello-World",
            "full_name": "octocat/Hello-World",
            "owner": {"login": "octocat"},
            "private": False,
            "html_url": "https://github.com/octocat/Hello-World",
            # ... other repo fields
        },
        "sender": {"login": "octocat"}
    }

@pytest.fixture
def sample_octopus_payload():
    # Simplified example - structure varies based on event type
    return {
        "Timestamp": "2024-04-14T10:00:00Z",
        "EventType": "SubscriptionPayload",
        "Payload": {
            "ServerUri": "https://octopus.example.com",
            "ServerAuditUri": "/app#/",
            "Subscription": {"Id": "subs-123"},
            "Event": {
                "Id": "evt-456",
                "RelatedDocumentIds": ["Projects-1", "Deployments-1", "Environments-1", "ServerTasks-1"],
                "Category": "DeploymentSucceeded",
                "UserId": "users-1",
                "Username": "deployer",
                "ProjectId": "Projects-1", # Added for direct access
                "ProjectName": "WebApp", # Added for card
                "EnvironmentId": "Environments-1", # Added for card
                "EnvironmentName": "Production", # Added for card
                "ReleaseVersion": "1.2.3", # Added for card
                "DeploymentId": "Deployments-1", # Added for card
                "TaskLogUrl": "/app#/tasks/ServerTasks-1", # Added for card
                "Message": "Deployment to Production succeeded for WebApp version 1.2.3",
                "Occurred": "2024-04-14T10:00:00Z",
            },
            "BatchId": "batch-789"
        }
    }

@pytest.fixture
def sample_conv_ref():
    """Creates a sample ConversationReference object for testing."""
    return ConversationReference(
        activity_id="act_webhook",
        user=ChannelAccount(id="user_wh", name="Webhook User", role="user"), # Often not relevant for webhook sends
        bot=ChannelAccount(id="bot_wh", name="Webhook Bot", role="bot"),
        conversation=ConversationAccount(id="TARGET_CONV_ID", name="Webhook Channel", is_group=True, conversation_type="channel", tenant_id="tenant_wh"),
        channel_id="msteams",
        service_url="https://service.url/wh/",
    )

# --- Mocks and Test Client ---

@pytest.fixture
def mock_adapter(mocker):
    return AsyncMock(spec=BotFrameworkAdapter)

@pytest.fixture
def mock_memory(mocker):
    memory = AsyncMock(spec=MemoryInterface)
    memory.get_conversation_reference = AsyncMock(return_value=None) # Default: not found
    # Patch the global current_memory used in main.py and webhook_handlers.py
    mocker.patch('main.current_memory', memory)
    mocker.patch('webhook_handlers.current_memory', memory)
    return memory

@pytest.fixture
def mock_background_tasks(mocker):
    tasks = MagicMock(spec=BackgroundTasks) # Use MagicMock for easier assertion
    tasks.add_task = MagicMock()
    # This fixture provides the mock object, but BackgroundTasks is usually injected by FastAPI
    # We will mock the specific add_task calls in tests or within the handlers
    return tasks

@pytest.fixture
def mock_settings_webhooks(mocker):
    """Mock settings specifically for webhook tests."""
    mock = MagicMock(spec=Settings)
    mock.GITHUB_WEBHOOK_SECRET = "test_github_secret"
    mock.OCTOPUS_WEBHOOK_SECRET = "test_octopus_secret"
    mock.GITHUB_REPO_CHANNEL_MAP = {"octocat/Hello-World": "TARGET_CONV_ID"}
    mock.OCTOPUS_PROJECT_CHANNEL_MAP = {"Projects-1": "TARGET_CONV_ID"}
    # Add other settings if needed by handlers

    # Patch settings used in main.py and webhook_handlers.py
    mocker.patch('main.settings', mock)
    mocker.patch('webhook_handlers.settings', mock)
    return mock

@pytest_asyncio.fixture
async def test_app(mock_settings_webhooks, mock_memory, mock_adapter):
    """Yield the FastAPI app instance configured with mocks."""
    # Ensure the app uses the mocked settings, adapter, etc. for the test session
    # This might require careful patching depending on how dependencies are injected/used at app startup
    # For now, assume patching main.settings etc. is sufficient
    fastapi_app.state.limiter.enabled = False # Disable rate limiting for tests
    yield fastapi_app

@pytest_asyncio.fixture
async def test_client(test_app):
    """Provides an HTTPX test client for the app."""
    async with AsyncClient(app=test_app, base_url="http://test") as client:
        yield client

# --- Test Cases (Endpoints) ---

@pytest.mark.asyncio
async def test_github_webhook_endpoint_success(test_client, sample_github_payload, mock_settings_webhooks, mocker, mock_adapter, mock_memory):
    """Test successful GitHub webhook request dispatching to handler."""
    repo_name = sample_github_payload["repository"]["full_name"]
    event_type = "pull_request"
    delivery_id = "test-delivery-id"
    raw_payload = json.dumps(sample_github_payload).encode('utf-8')

    # Mock signature verification to succeed
    mock_verify_sig = mocker.patch('main.verify_github_signature', return_value=True)

    # Mock the actual handler function to check if it was scheduled
    mock_handler = mocker.patch('webhook_handlers.process_github_event', new_callable=AsyncMock)

    # Mock BackgroundTasks.add_task called within the endpoint
    # We need to capture the instance FastAPI creates or patch where it's called from.
    # Patching 'main.process_github_event' indirectly tests if add_task was called with it.
    # Let's refine this by patching add_task on the BackgroundTasks instance passed to the handler
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance) # Mock the class used for injection

    # Generate expected signature
    secret = mock_settings_webhooks.GITHUB_WEBHOOK_SECRET
    expected_sig = "sha256=" + hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Event": event_type,
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": expected_sig,
        "Content-Type": "application/json"
    }

    # Need to pass the mocked BackgroundTasks explicitly if dependency override is needed,
    # but patching the class 'main.BackgroundTasks' should cover the injection.
    # Override dependency if patching the class doesn't work as expected:
    # app.dependency_overrides[BackgroundTasks] = lambda: mock_bg_tasks_instance

    response = await test_client.post("/api/webhooks/github", json=sample_github_payload, headers=headers)

    # app.dependency_overrides = {} # Clean up override

    assert response.status_code == 202
    mock_verify_sig.assert_called_once_with(raw_payload, secret, expected_sig)

    # Check that the handler was scheduled via BackgroundTasks.add_task
    mock_bg_tasks_instance.add_task.assert_called_once()
    # Get the arguments add_task was called with
    call_args, call_kwargs = mock_bg_tasks_instance.add_task.call_args
    # Assert the first argument is the handler function itself
    assert call_args[0] == webhook_handlers.process_github_event
    # Assert specific arguments passed to the handler
    assert call_kwargs['event_type'] == event_type
    assert call_kwargs['payload'] == sample_github_payload
    assert call_kwargs['repo_name'] == repo_name
    assert call_kwargs['adapter'] is mock_adapter # Check adapter is passed (assuming main.ADAPTER is used)
    assert call_kwargs['current_memory'] is mock_memory # Check memory is passed
    assert call_kwargs['settings'] is mock_settings_webhooks # Check settings is passed

@pytest.mark.asyncio
async def test_github_webhook_endpoint_invalid_signature(test_client, sample_github_payload, mock_settings_webhooks, mocker):
    """Test GitHub webhook request with an invalid signature."""
    # Mock signature verification to fail
    mock_verify_sig = mocker.patch('main.verify_github_signature', return_value=False)
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "test-delivery-id-invalid",
        "X-Hub-Signature-256": "sha256=invalid_signature",
        "Content-Type": "application/json"
    }

    response = await test_client.post("/api/webhooks/github", json=sample_github_payload, headers=headers)

    assert response.status_code == 403
    assert "Invalid GitHub signature" in response.text
    mock_verify_sig.assert_called_once()
    mock_bg_tasks_instance.add_task.assert_not_called() # Handler should not be scheduled

@pytest.mark.asyncio
async def test_github_webhook_endpoint_missing_signature(test_client, sample_github_payload, mock_settings_webhooks, mocker):
    """Test GitHub webhook request when secret is configured but signature is missing."""
    mock_verify_sig = mocker.patch('main.verify_github_signature', return_value=False) # Assume verification fails if sig is None
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    # Signature header is missing
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "test-delivery-id-no-sig",
        "Content-Type": "application/json"
    }
    raw_payload = json.dumps(sample_github_payload).encode('utf-8')

    response = await test_client.post("/api/webhooks/github", json=sample_github_payload, headers=headers)

    assert response.status_code == 403
    assert "Invalid GitHub signature" in response.text
    # Verify it was called with None signature
    mock_verify_sig.assert_called_once_with(raw_payload, mock_settings_webhooks.GITHUB_WEBHOOK_SECRET, None)
    mock_bg_tasks_instance.add_task.assert_not_called()

@pytest.mark.asyncio
async def test_github_webhook_endpoint_secret_not_configured(test_client, sample_github_payload, mock_settings_webhooks, mocker, mock_adapter, mock_memory):
    """Test GitHub webhook request proceeds when secret is not configured."""
    mock_settings_webhooks.GITHUB_WEBHOOK_SECRET = None # Simulate secret not set
    mock_verify_sig = mocker.patch('main.verify_github_signature') # Should not be called
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "test-delivery-id-no-secret",
        "Content-Type": "application/json"
    }

    response = await test_client.post("/api/webhooks/github", json=sample_github_payload, headers=headers)

    assert response.status_code == 202
    mock_verify_sig.assert_not_called() # Verification skipped
    mock_bg_tasks_instance.add_task.assert_called_once() # Handler should be scheduled

@pytest.mark.asyncio
async def test_github_webhook_endpoint_missing_repo(test_client, sample_github_payload, mock_settings_webhooks, mocker):
    """Test GitHub webhook request with missing repository info in payload."""
    invalid_payload = sample_github_payload.copy()
    # Simulate missing full_name which is used by the endpoint
    if "repository" in invalid_payload and "full_name" in invalid_payload["repository"]:
         del invalid_payload["repository"]["full_name"]
    elif "repository" in invalid_payload:
         del invalid_payload["repository"]

    # Mock signature verification to succeed
    mock_verify_sig = mocker.patch('main.verify_github_signature', return_value=True)
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    secret = mock_settings_webhooks.GITHUB_WEBHOOK_SECRET
    raw_payload = json.dumps(invalid_payload).encode('utf-8')
    expected_sig = "sha256=" + hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha256).hexdigest()

    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "test-delivery-id-no-repo",
        "X-Hub-Signature-256": expected_sig,
        "Content-Type": "application/json"
    }

    response = await test_client.post("/api/webhooks/github", json=invalid_payload, headers=headers)

    assert response.status_code == 400
    assert "Missing repository name" in response.text
    mock_bg_tasks_instance.add_task.assert_not_called()

@pytest.mark.asyncio
async def test_github_webhook_endpoint_missing_event_type(test_client, sample_github_payload, mock_settings_webhooks, mocker):
    """Test GitHub webhook request with missing event type header."""
    # Mock signature verification to succeed
    mock_verify_sig = mocker.patch('main.verify_github_signature', return_value=True)
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    secret = mock_settings_webhooks.GITHUB_WEBHOOK_SECRET
    raw_payload = json.dumps(sample_github_payload).encode('utf-8')
    expected_sig = "sha256=" + hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha256).hexdigest()

    # Missing X-GitHub-Event header
    headers = {
        "X-GitHub-Delivery": "test-delivery-id-no-event",
        "X-Hub-Signature-256": expected_sig,
        "Content-Type": "application/json"
    }

    response = await test_client.post("/api/webhooks/github", json=sample_github_payload, headers=headers)

    assert response.status_code == 400
    assert "Missing event type" in response.text
    mock_bg_tasks_instance.add_task.assert_not_called()

@pytest.mark.asyncio
async def test_placeholder_webhook_endpoint():
    assert True

# --- Test Cases (Handlers) ---

@pytest.mark.asyncio
async def test_process_github_event_success(mock_adapter, mock_background_tasks, mock_memory, mock_settings_webhooks, sample_github_payload, sample_conv_ref, mocker):
    """Test successful processing of a GitHub event (PR opened)."""
    repo_name = sample_github_payload["repository"]["full_name"]
    event_type = "pull_request"
    target_conv_id = mock_settings_webhooks.GITHUB_REPO_CHANNEL_MAP[repo_name]
    mock_card_payload = {"type": "AdaptiveCard", "version": "1.5", "body": ["Mock GitHub Card"]}

    # Mock memory to return the conversation reference
    mock_memory.get_conversation_reference.return_value = sample_conv_ref
    # Mock card generation
    mock_create_card = mocker.patch('webhook_handlers.create_github_pr_notification_card', return_value=mock_card_payload)
    # Mock the proactive message sending function that background_tasks calls
    mock_send_proactive = mocker.patch('webhook_handlers.send_proactive_message', new_callable=AsyncMock)

    await webhook_handlers.process_github_event(
        adapter=mock_adapter,
        background_tasks=mock_background_tasks,
        current_memory=mock_memory,
        settings=mock_settings_webhooks,
        event_type=event_type,
        payload=sample_github_payload,
        repo_name=repo_name
    )

    # Verify memory was checked
    mock_memory.get_conversation_reference.assert_awaited_once_with(target_conv_id)
    # Verify card was generated
    mock_create_card.assert_called_once_with(sample_github_payload)
    # Verify background task was added to send the message
    mock_background_tasks.add_task.assert_called_once()
    call_args, call_kwargs = mock_background_tasks.add_task.call_args
    assert call_args[0] == utils.helpers.send_proactive_message # Check correct function scheduled
    assert call_kwargs['adapter'] is mock_adapter
    assert call_kwargs['conversation_reference'] is sample_conv_ref
    assert call_kwargs['attachments'] == [mock_card_payload]

@pytest.mark.asyncio
async def test_process_github_event_no_channel_map(mock_adapter, mock_background_tasks, mock_memory, mock_settings_webhooks, sample_github_payload, mocker):
    """Test GitHub event when no channel mapping exists for the repo."""
    repo_name = "unknown/repo"
    mock_logger_warning = mocker.patch('webhook_handlers.logger.warning')

    await webhook_handlers.process_github_event(
        adapter=mock_adapter,
        background_tasks=mock_background_tasks,
        current_memory=mock_memory,
        settings=mock_settings_webhooks,
        event_type="pull_request",
        payload=sample_github_payload,
        repo_name=repo_name # This repo is not in the map
    )

    # Verify memory was not checked, no card generated, no task added
    mock_memory.get_conversation_reference.assert_not_awaited()
    mock_background_tasks.add_task.assert_not_called()
    mock_logger_warning.assert_called_once_with(f"No channel mapping found for GitHub repository: {repo_name}")

@pytest.mark.asyncio
async def test_process_github_event_no_conv_ref(mock_adapter, mock_background_tasks, mock_memory, mock_settings_webhooks, sample_github_payload, mocker):
    """Test GitHub event when conversation reference is not found."""
    repo_name = sample_github_payload["repository"]["full_name"]
    target_conv_id = mock_settings_webhooks.GITHUB_REPO_CHANNEL_MAP[repo_name]
    mock_logger_error = mocker.patch('webhook_handlers.logger.error')

    # Mock memory to return None (conv ref not found)
    mock_memory.get_conversation_reference.return_value = None

    await webhook_handlers.process_github_event(
        adapter=mock_adapter,
        background_tasks=mock_background_tasks,
        current_memory=mock_memory,
        settings=mock_settings_webhooks,
        event_type="pull_request",
        payload=sample_github_payload,
        repo_name=repo_name
    )

    mock_memory.get_conversation_reference.assert_awaited_once_with(target_conv_id)
    mock_background_tasks.add_task.assert_not_called()
    mock_logger_error.assert_called_once_with(f"Conversation reference not found for channel ID: {target_conv_id} (GitHub Repo: {repo_name})")

@pytest.mark.asyncio
async def test_process_github_event_no_card_generated(mock_adapter, mock_background_tasks, mock_memory, mock_settings_webhooks, sample_github_payload, sample_conv_ref, mocker):
    """Test GitHub event when the event type/action doesn't generate a card."""
    repo_name = sample_github_payload["repository"]["full_name"]
    event_type = "issue_comment" # Assume this is not handled
    payload = {"action": "created", "repository": sample_github_payload["repository"]} # Minimal payload
    target_conv_id = mock_settings_webhooks.GITHUB_REPO_CHANNEL_MAP[repo_name]
    mock_logger_info = mocker.patch('webhook_handlers.logger.info')

    mock_memory.get_conversation_reference.return_value = sample_conv_ref
    # Mock card generation to return None (or just don't patch it if default is None)
    mock_create_card = mocker.patch('webhook_handlers.create_github_pr_notification_card', return_value=None)

    await webhook_handlers.process_github_event(
        adapter=mock_adapter,
        background_tasks=mock_background_tasks,
        current_memory=mock_memory,
        settings=mock_settings_webhooks,
        event_type=event_type,
        payload=payload,
        repo_name=repo_name
    )

    mock_memory.get_conversation_reference.assert_awaited_once_with(target_conv_id)
    # Ensure the card function wasn't called OR returned None (depending on implementation)
    # If only PR card exists, it wouldn't be called for issue_comment
    mock_create_card.assert_not_called() # Adjust if other card funcs exist
    mock_background_tasks.add_task.assert_not_called()
    # Check for the specific info log
    mock_logger_info.assert_any_call(f"No card generated for GitHub event type '{event_type}' and action '{payload.get('action')}' in repo '{repo_name}'.")

# --- Tests for Octopus Endpoint (to be added) ---

@pytest.mark.asyncio
async def test_octopus_webhook_endpoint_success(test_client, sample_octopus_payload, mock_settings_webhooks, mocker, mock_adapter, mock_memory):
    """Test successful Octopus webhook request dispatching to handler."""
    project_id = sample_octopus_payload["Payload"]["Event"]["ProjectId"]
    event_category = sample_octopus_payload["Payload"]["Event"]["Category"]
    raw_payload = json.dumps(sample_octopus_payload).encode('utf-8')

    # Mock signature verification to succeed
    mock_verify_sig = mocker.patch('main.verify_octopus_signature', return_value=True)

    # Mock the actual handler function
    mock_handler = mocker.patch('webhook_handlers.process_octopus_event', new_callable=AsyncMock)
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    # Generate a plausible signature (actual verification logic is mocked)
    secret = mock_settings_webhooks.OCTOPUS_WEBHOOK_SECRET
    # Note: Octopus signature might use different algo/format, but mocking verify func bypasses this
    expected_sig = "sha1=" + hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha1).hexdigest()

    headers = {
        "X-Octopus-Signature": expected_sig, # Header name might vary
        "Content-Type": "application/json"
    }

    response = await test_client.post("/api/webhooks/octopus", json=sample_octopus_payload, headers=headers)

    assert response.status_code == 202
    mock_verify_sig.assert_called_once_with(raw_payload, secret, expected_sig)

    # Check handler scheduling
    mock_bg_tasks_instance.add_task.assert_called_once()
    call_args, call_kwargs = mock_bg_tasks_instance.add_task.call_args
    assert call_args[0] == webhook_handlers.process_octopus_event
    assert call_kwargs['event_category'] == event_category
    assert call_kwargs['payload'] == sample_octopus_payload
    assert call_kwargs['project_id'] == project_id
    assert call_kwargs['adapter'] is mock_adapter
    assert call_kwargs['current_memory'] is mock_memory
    assert call_kwargs['settings'] is mock_settings_webhooks

@pytest.mark.asyncio
async def test_octopus_webhook_endpoint_invalid_signature(test_client, sample_octopus_payload, mock_settings_webhooks, mocker):
    """Test Octopus webhook request with an invalid signature."""
    mock_verify_sig = mocker.patch('main.verify_octopus_signature', return_value=False)
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    headers = {
        "X-Octopus-Signature": "sha1=invalid_signature",
        "Content-Type": "application/json"
    }

    response = await test_client.post("/api/webhooks/octopus", json=sample_octopus_payload, headers=headers)

    assert response.status_code == 403
    assert "Invalid Octopus signature" in response.text
    mock_verify_sig.assert_called_once()
    mock_bg_tasks_instance.add_task.assert_not_called()

@pytest.mark.asyncio
async def test_octopus_webhook_endpoint_missing_signature(test_client, sample_octopus_payload, mock_settings_webhooks, mocker):
    """Test Octopus webhook request when secret is configured but signature is missing."""
    mock_verify_sig = mocker.patch('main.verify_octopus_signature', return_value=False) # Assume verification fails if sig is None
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    headers = {"Content-Type": "application/json"} # Signature header missing
    raw_payload = json.dumps(sample_octopus_payload).encode('utf-8')

    response = await test_client.post("/api/webhooks/octopus", json=sample_octopus_payload, headers=headers)

    assert response.status_code == 403
    assert "Invalid Octopus signature" in response.text
    mock_verify_sig.assert_called_once_with(raw_payload, mock_settings_webhooks.OCTOPUS_WEBHOOK_SECRET, None)
    mock_bg_tasks_instance.add_task.assert_not_called()

@pytest.mark.asyncio
async def test_octopus_webhook_endpoint_secret_not_configured(test_client, sample_octopus_payload, mock_settings_webhooks, mocker, mock_adapter, mock_memory):
    """Test Octopus webhook request proceeds when secret is not configured."""
    mock_settings_webhooks.OCTOPUS_WEBHOOK_SECRET = None
    mock_verify_sig = mocker.patch('main.verify_octopus_signature') # Should return True if secret is None
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    headers = {"Content-Type": "application/json"}

    response = await test_client.post("/api/webhooks/octopus", json=sample_octopus_payload, headers=headers)

    assert response.status_code == 202
    # verify_octopus_signature should still be called, but return True internally when secret is None
    mock_verify_sig.assert_called_once_with(mocker.ANY, None, None)
    mock_bg_tasks_instance.add_task.assert_called_once() # Handler scheduled

@pytest.mark.asyncio
async def test_octopus_webhook_endpoint_missing_project_id(test_client, sample_octopus_payload, mock_settings_webhooks, mocker):
    """Test Octopus webhook request with missing project ID in payload."""
    invalid_payload = sample_octopus_payload.copy()
    # Simulate missing ProjectId
    if "Payload" in invalid_payload and "Event" in invalid_payload["Payload"] and "ProjectId" in invalid_payload["Payload"]["Event"]:
        del invalid_payload["Payload"]["Event"]["ProjectId"]

    mock_verify_sig = mocker.patch('main.verify_octopus_signature', return_value=True)
    mock_bg_tasks_instance = MagicMock(spec=BackgroundTasks)
    mocker.patch('main.BackgroundTasks', return_value=mock_bg_tasks_instance)

    secret = mock_settings_webhooks.OCTOPUS_WEBHOOK_SECRET
    raw_payload = json.dumps(invalid_payload).encode('utf-8')
    expected_sig = "sha1=" + hmac.new(secret.encode('utf-8'), raw_payload, hashlib.sha1).hexdigest()

    headers = {
        "X-Octopus-Signature": expected_sig,
        "Content-Type": "application/json"
    }

    response = await test_client.post("/api/webhooks/octopus", json=invalid_payload, headers=headers)

    assert response.status_code == 400
    assert "Missing project ID" in response.text
    mock_bg_tasks_instance.add_task.assert_not_called()

    mock_bg_tasks_instance.add_task.assert_not_called()

# --- Tests for Octopus Handler (to be added) ---

@pytest.mark.asyncio
async def test_process_octopus_event_success(mock_adapter, mock_background_tasks, mock_memory, mock_settings_webhooks, sample_octopus_payload, sample_conv_ref, mocker):
    """Test successful processing of an Octopus event (DeploymentSucceeded)."""
    project_id = sample_octopus_payload["Payload"]["Event"]["ProjectId"]
    event_category = sample_octopus_payload["Payload"]["Event"]["Category"]
    target_conv_id = mock_settings_webhooks.OCTOPUS_PROJECT_CHANNEL_MAP[project_id]
    mock_card_payload = {"type": "AdaptiveCard", "version": "1.5", "body": ["Mock Octopus Card"]}

    # Mock memory to return the conversation reference
    mock_memory.get_conversation_reference.return_value = sample_conv_ref
    # Mock card generation
    mock_create_card = mocker.patch('webhook_handlers.create_octopus_deployment_notification_card', return_value=mock_card_payload)
    # Mock proactive message sending
    mock_send_proactive = mocker.patch('webhook_handlers.send_proactive_message', new_callable=AsyncMock)

    await webhook_handlers.process_octopus_event(
        adapter=mock_adapter,
        background_tasks=mock_background_tasks,
        current_memory=mock_memory,
        settings=mock_settings_webhooks,
        event_category=event_category,
        payload=sample_octopus_payload,
        project_id=project_id
    )

    mock_memory.get_conversation_reference.assert_awaited_once_with(target_conv_id)
    mock_create_card.assert_called_once_with(sample_octopus_payload)
    # Verify background task was added
    mock_background_tasks.add_task.assert_called_once()
    call_args, call_kwargs = mock_background_tasks.add_task.call_args
    assert call_args[0] == utils.helpers.send_proactive_message
    assert call_kwargs['adapter'] is mock_adapter
    assert call_kwargs['conversation_reference'] is sample_conv_ref
    assert call_kwargs['attachments'] == [mock_card_payload]

@pytest.mark.asyncio
async def test_process_octopus_event_no_channel_map(mock_adapter, mock_background_tasks, mock_memory, mock_settings_webhooks, sample_octopus_payload, mocker):
    """Test Octopus event when no channel mapping exists for the project."""
    project_id = "Unknown-Project-ID"
    mock_logger_warning = mocker.patch('webhook_handlers.logger.warning')

    await webhook_handlers.process_octopus_event(
        adapter=mock_adapter,
        background_tasks=mock_background_tasks,
        current_memory=mock_memory,
        settings=mock_settings_webhooks,
        event_category="DeploymentSucceeded",
        payload=sample_octopus_payload,
        project_id=project_id # This project is not in the map
    )

    mock_memory.get_conversation_reference.assert_not_awaited()
    mock_background_tasks.add_task.assert_not_called()
    mock_logger_warning.assert_called_once_with(f"No channel mapping found for Octopus project ID: {project_id}")

@pytest.mark.asyncio
async def test_process_octopus_event_no_conv_ref(mock_adapter, mock_background_tasks, mock_memory, mock_settings_webhooks, sample_octopus_payload, mocker):
    """Test Octopus event when conversation reference is not found."""
    project_id = sample_octopus_payload["Payload"]["Event"]["ProjectId"]
    target_conv_id = mock_settings_webhooks.OCTOPUS_PROJECT_CHANNEL_MAP[project_id]
    mock_logger_error = mocker.patch('webhook_handlers.logger.error')

    # Mock memory to return None
    mock_memory.get_conversation_reference.return_value = None

    await webhook_handlers.process_octopus_event(
        adapter=mock_adapter,
        background_tasks=mock_background_tasks,
        current_memory=mock_memory,
        settings=mock_settings_webhooks,
        event_category="DeploymentSucceeded",
        payload=sample_octopus_payload,
        project_id=project_id
    )

    mock_memory.get_conversation_reference.assert_awaited_once_with(target_conv_id)
    mock_background_tasks.add_task.assert_not_called()
    mock_logger_error.assert_called_once_with(f"Conversation reference not found for channel ID: {target_conv_id} (Octopus Project: {project_id})")

@pytest.mark.asyncio
async def test_process_octopus_event_no_card_generated(mock_adapter, mock_background_tasks, mock_memory, mock_settings_webhooks, sample_octopus_payload, sample_conv_ref, mocker):
    """Test Octopus event when the event category doesn't generate a card."""
    project_id = sample_octopus_payload["Payload"]["Event"]["ProjectId"]
    event_category = "SubscriptionModified" # Assume this is not handled
    payload = {"Payload": {"Event": {"ProjectId": project_id, "Category": event_category}}} # Minimal
    target_conv_id = mock_settings_webhooks.OCTOPUS_PROJECT_CHANNEL_MAP[project_id]
    mock_logger_info = mocker.patch('webhook_handlers.logger.info')

    mock_memory.get_conversation_reference.return_value = sample_conv_ref
    # Mock card generation to return None
    mock_create_card = mocker.patch('webhook_handlers.create_octopus_deployment_notification_card', return_value=None)

    await webhook_handlers.process_octopus_event(
        adapter=mock_adapter,
        background_tasks=mock_background_tasks,
        current_memory=mock_memory,
        settings=mock_settings_webhooks,
        event_category=event_category,
        payload=payload,
        project_id=project_id
    )

    mock_memory.get_conversation_reference.assert_awaited_once_with(target_conv_id)
    mock_create_card.assert_not_called() # As only DeploymentS/F is checked
    mock_background_tasks.add_task.assert_not_called()
    mock_logger_info.assert_any_call(f"No card generated for Octopus event category '{event_category}' in project '{project_id}'.") 