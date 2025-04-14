import pytest
import pytest_asyncio
import respx
from httpx import Response, Request, AsyncClient
from pytest_mock import MockerFixture
from botbuilder.core import TurnContext
from botbuilder.schema import Activity, ConversationAccount, ChannelAccount

from app.config import Settings
from app.memory.memory_base import MemoryBase
from app.tools.octopus_tools import (
    octopus_trigger_deployment,
    OctopusTriggerDeploymentParams,
)
# Assume client getter exists and might raise errors
from app.utils.api_clients import get_octopus_client, OctopusClientError


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
        OCTOPUS_SERVER_URL="https://test.octopus.app",
        OCTOPUS_API_KEY="API-TESTKEY",
        OCTOPUS_SPACE_ID="Spaces-1",
        TOOL_ERROR_CARD_TITLE="Tool Error",
    )

@pytest.fixture
def mock_context(mocker: MockerFixture) -> TurnContext:
    """Mocks TurnContext."""
    mock_activity = Activity(
        type="message",
        channel_id="msteams",
        conversation=ConversationAccount(id="conv_id"),
        recipient=ChannelAccount(id="bot_id"),
        from_property=ChannelAccount(id="user_id", name="Test User"),
        text="deploy something",
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
def mock_memory(mocker: MockerFixture) -> MemoryBase:
    """Mocks the MemoryBase interface."""
    memory = mocker.AsyncMock(spec=MemoryBase)
    memory.get_conversation_reference.return_value = mocker.MagicMock()
    memory.get_user_data.return_value = {}
    return memory

@pytest_asyncio.fixture
async def mock_octopus_client(mocker: MockerFixture, mock_settings: Settings) -> AsyncClient:
    """Mocks the httpx AsyncClient used for Octopus."""
    # We use respx to mock HTTP calls, but we still need to mock the getter
    # in case it does more than just create a client (e.g., checks settings).
    # If get_octopus_client just returns httpx.AsyncClient(**config),
    # we might not strictly *need* this mock, but it's safer.
    mock_client = AsyncClient(base_url=mock_settings.OCTOPUS_SERVER_URL) # Real client for respx
    mocker.patch("app.utils.api_clients.get_octopus_client", return_value=mock_client)
    return mock_client


# --- Test Octopus Trigger Deployment ---

@pytest.mark.asyncio
@respx.mock
async def test_octopus_trigger_deployment_success(
    respx_mock, # respx.mock automatically provides this when decorating test
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_octopus_client: AsyncClient, # Included to ensure getter patching
    mocker: MockerFixture,
):
    """Tests successful Octopus deployment trigger."""
    project_name = "MyWebApp"
    environment_name = "Production"
    params = OctopusTriggerDeploymentParams(project_name=project_name, environment_name=environment_name)

    project_id = "Projects-123"
    environment_id = "Environments-456"
    deployment_id = "Deployments-789"
    task_id = "ServerTasks-101"
    space_id = mock_settings.OCTOPUS_SPACE_ID
    base_url = mock_settings.OCTOPUS_SERVER_URL

    # 1. Mock Project Lookup
    project_lookup_url = f"{base_url}/api/{space_id}/projects?name={project_name}"
    respx_mock.get(project_lookup_url).mock(return_value=Response(200, json={
        "Items": [{"Id": project_id, "Name": project_name}]
    }))

    # 2. Mock Environment Lookup
    env_lookup_url = f"{base_url}/api/{space_id}/environments?name={environment_name}"
    respx_mock.get(env_lookup_url).mock(return_value=Response(200, json={
        "Items": [{"Id": environment_id, "Name": environment_name}]
    }))

    # 3. Mock Deployment Trigger POST
    deployment_url = f"{base_url}/api/{space_id}/deployments"
    respx_mock.post(deployment_url).mock(return_value=Response(201, json={
        "Id": deployment_id,
        "ProjectId": project_id,
        "EnvironmentId": environment_id,
        "TaskId": task_id,
        "Links": {
            "Web": f"/app#/{space_id}/tasks/{task_id}"
        }
    }))

    mocker.patch("app.tools.octopus_tools.create_adaptive_card_response", return_value=mocker.MagicMock()) # Mock card response

    # --- Act ---
    await octopus_trigger_deployment(mock_context, mock_memory, mock_settings, params)

    # --- Assert ---
    # Check calls were made
    assert respx_mock.calls.call_count == 3
    assert respx_mock.calls[0].request.url == project_lookup_url
    assert respx_mock.calls[1].request.url == env_lookup_url
    assert respx_mock.calls[2].request.url == deployment_url

    # Check POST request body
    request_body = respx_mock.calls[2].request.content
    import json
    payload = json.loads(request_body)
    assert payload["ProjectId"] == project_id
    assert payload["EnvironmentId"] == environment_id

    # Check headers (API Key)
    assert respx_mock.calls[0].request.headers["X-Octopus-ApiKey"] == mock_settings.OCTOPUS_API_KEY
    assert respx_mock.calls[1].request.headers["X-Octopus-ApiKey"] == mock_settings.OCTOPUS_API_KEY
    assert respx_mock.calls[2].request.headers["X-Octopus-ApiKey"] == mock_settings.OCTOPUS_API_KEY

    # Check success message/card was sent
    mock_context.send_activity.assert_called_once()
    card_mock = mocker.patch("app.tools.octopus_tools.create_adaptive_card_response")
    card_mock.assert_called_once()
    # Optionally check card content for deployment link etc.
    # args, kwargs = card_mock.call_args
    # card_payload = kwargs.get('card_payload', {})
    # assert f"{base_url}/app#/{space_id}/tasks/{task_id}" in json.dumps(card_payload)

# --- Add more tests for error scenarios ---

@pytest.mark.asyncio
@respx.mock
async def test_octopus_trigger_deployment_project_not_found(
    respx_mock,
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_octopus_client: AsyncClient,
    mocker: MockerFixture,
):
    """Tests when the Octopus project is not found."""
    project_name = "NonExistentProject"
    environment_name = "Production"
    params = OctopusTriggerDeploymentParams(project_name=project_name, environment_name=environment_name)

    space_id = mock_settings.OCTOPUS_SPACE_ID
    base_url = mock_settings.OCTOPUS_SERVER_URL

    # Mock Project Lookup (Not Found - empty list)
    project_lookup_url = f"{base_url}/api/{space_id}/projects?name={project_name}"
    respx_mock.get(project_lookup_url).mock(return_value=Response(200, json={"Items": []}))

    mocker.patch("app.tools.octopus_tools.create_error_card_response", return_value=mocker.MagicMock())

    # --- Act ---
    await octopus_trigger_deployment(mock_context, mock_memory, mock_settings, params)

    # --- Assert ---
    assert respx_mock.calls.call_count == 1 # Only project lookup called
    assert respx_mock.calls[0].request.url == project_lookup_url

    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.octopus_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert f"Could not find Octopus project named '{project_name}'" in args[1]


@pytest.mark.asyncio
@respx.mock
async def test_octopus_trigger_deployment_environment_not_found(
    respx_mock,
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_octopus_client: AsyncClient,
    mocker: MockerFixture,
):
    """Tests when the Octopus environment is not found."""
    project_name = "MyWebApp"
    environment_name = "NonExistentEnv"
    params = OctopusTriggerDeploymentParams(project_name=project_name, environment_name=environment_name)

    project_id = "Projects-123"
    space_id = mock_settings.OCTOPUS_SPACE_ID
    base_url = mock_settings.OCTOPUS_SERVER_URL

    # Mock Project Lookup (Success)
    project_lookup_url = f"{base_url}/api/{space_id}/projects?name={project_name}"
    respx_mock.get(project_lookup_url).mock(return_value=Response(200, json={
        "Items": [{"Id": project_id, "Name": project_name}]
    }))

    # Mock Environment Lookup (Not Found - empty list)
    env_lookup_url = f"{base_url}/api/{space_id}/environments?name={environment_name}"
    respx_mock.get(env_lookup_url).mock(return_value=Response(200, json={"Items": []}))

    mocker.patch("app.tools.octopus_tools.create_error_card_response", return_value=mocker.MagicMock())

    # --- Act ---
    await octopus_trigger_deployment(mock_context, mock_memory, mock_settings, params)

    # --- Assert ---
    assert respx_mock.calls.call_count == 2 # Project and Env lookup called
    assert respx_mock.calls[0].request.url == project_lookup_url
    assert respx_mock.calls[1].request.url == env_lookup_url

    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.octopus_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert f"Could not find Octopus environment named '{environment_name}'" in args[1]


@pytest.mark.asyncio
@respx.mock
async def test_octopus_trigger_deployment_api_error(
    respx_mock,
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_octopus_client: AsyncClient,
    mocker: MockerFixture,
):
    """Tests handling of API errors during deployment trigger."""
    project_name = "MyWebApp"
    environment_name = "Production"
    params = OctopusTriggerDeploymentParams(project_name=project_name, environment_name=environment_name)

    project_id = "Projects-123"
    environment_id = "Environments-456"
    space_id = mock_settings.OCTOPUS_SPACE_ID
    base_url = mock_settings.OCTOPUS_SERVER_URL

    # Mock Project Lookup
    project_lookup_url = f"{base_url}/api/{space_id}/projects?name={project_name}"
    respx_mock.get(project_lookup_url).mock(return_value=Response(200, json={
        "Items": [{"Id": project_id, "Name": project_name}]
    }))

    # Mock Environment Lookup
    env_lookup_url = f"{base_url}/api/{space_id}/environments?name={environment_name}"
    respx_mock.get(env_lookup_url).mock(return_value=Response(200, json={
        "Items": [{"Id": environment_id, "Name": environment_name}]
    }))

    # Mock Deployment Trigger POST (Error)
    deployment_url = f"{base_url}/api/{space_id}/deployments"
    error_message = "Deployment failed: Missing variables"
    respx_mock.post(deployment_url).mock(return_value=Response(400, json={
        "ErrorMessage": error_message,
        "Errors": ["Variable X is missing"]
    }))

    mocker.patch("app.tools.octopus_tools.create_error_card_response", return_value=mocker.MagicMock())

    # --- Act ---
    await octopus_trigger_deployment(mock_context, mock_memory, mock_settings, params)

    # --- Assert ---
    assert respx_mock.calls.call_count == 3

    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.octopus_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Error triggering Octopus deployment" in args[1]
    assert "Status 400" in args[1]
    assert error_message in args[1]


@pytest.mark.asyncio
async def test_octopus_trigger_deployment_client_unavailable(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture,
):
    """Tests handling when the Octopus client cannot be obtained."""
    params = OctopusTriggerDeploymentParams(project_name="AnyProject", environment_name="AnyEnv")
    mocker.patch("app.utils.api_clients.get_octopus_client", side_effect=OctopusClientError("Invalid API Key"))
    mocker.patch("app.tools.octopus_tools.create_error_card_response", return_value=mocker.MagicMock())

    # --- Act ---
    await octopus_trigger_deployment(mock_context, mock_memory, mock_settings, params)

    # --- Assert ---
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.octopus_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Failed to initialize Octopus client" in args[1]
    assert "Invalid API Key" in args[1] 