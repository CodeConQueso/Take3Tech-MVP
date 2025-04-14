import pytest
import pytest_asyncio
import respx
from httpx import Response, Request, AsyncClient
from pytest_mock import MockerFixture
from botbuilder.core import TurnContext
from botbuilder.schema import Activity, ConversationAccount, ChannelAccount

from app.config import Settings
from app.memory.memory_base import MemoryBase
from app.tools.search_tools import (
    search_perplexity,
    SearchPerplexityParams,
)
# Assume client getter exists and might raise errors
from app.utils.api_clients import get_perplexity_client, PerplexityClientError


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
        PERPLEXITY_API_KEY="pplx-testkey",
        TOOL_ERROR_CARD_TITLE="Tool Error",
        # Add other relevant settings
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
        text="search for something",
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
async def mock_perplexity_client(mocker: MockerFixture, mock_settings: Settings) -> AsyncClient:
    """Mocks the httpx AsyncClient used for Perplexity."""
    mock_client = AsyncClient(base_url="https://api.perplexity.ai") # Real client for respx
    mocker.patch("app.utils.api_clients.get_perplexity_client", return_value=mock_client)
    return mock_client


# --- Test Search Perplexity ---

@pytest.mark.asyncio
@respx.mock
async def test_search_perplexity_success(
    respx_mock,
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_perplexity_client: AsyncClient,
    mocker: MockerFixture,
):
    """Tests successful Perplexity search."""
    search_query = "What is the airspeed velocity of an unladen swallow?"
    params = SearchPerplexityParams(query=search_query)

    api_url = "https://api.perplexity.ai/chat/completions"
    expected_response_content = "African or European swallow?"

    # Mock the Perplexity API call
    respx_mock.post(api_url).mock(return_value=Response(200, json={
        "id": "test-chat-id",
        "model": "pplx-70b-online",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": expected_response_content
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    }))

    mocker.patch("app.tools.search_tools.create_adaptive_card_response", return_value=mocker.MagicMock()) # Mock card response

    # --- Act ---
    await search_perplexity(mock_context, mock_memory, mock_settings, params)

    # --- Assert ---
    # Check API call details
    assert respx_mock.calls.call_count == 1
    request = respx_mock.calls[0].request
    assert request.url == api_url
    assert request.method == "POST"
    assert f"Bearer {mock_settings.PERPLEXITY_API_KEY}" in request.headers.get("Authorization", "")
    assert request.headers.get("Content-Type") == "application/json"

    # Check request body
    import json
    payload = json.loads(request.content)
    assert payload["model"] == "pplx-70b-online"
    assert any(msg["role"] == "user" and msg["content"] == search_query for msg in payload["messages"])

    # Check success message/card was sent
    mock_context.send_activity.assert_called_once()
    card_mock = mocker.patch("app.tools.search_tools.create_adaptive_card_response")
    card_mock.assert_called_once()
    # Optionally check card content for the response
    # args, kwargs = card_mock.call_args
    # card_payload = kwargs.get('card_payload', {})
    # assert expected_response_content in json.dumps(card_payload)


# --- Add more tests for error scenarios ---

@pytest.mark.asyncio
@respx.mock
async def test_search_perplexity_api_error(
    respx_mock,
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mock_perplexity_client: AsyncClient,
    mocker: MockerFixture,
):
    """Tests handling of API errors from Perplexity."""
    search_query = "Why is the sky blue?"
    params = SearchPerplexityParams(query=search_query)

    api_url = "https://api.perplexity.ai/chat/completions"
    error_detail = "Invalid API key provided."

    # Mock the Perplexity API call (Error)
    respx_mock.post(api_url).mock(return_value=Response(401, json={
        "error": {
            "message": error_detail,
            "type": "invalid_request_error",
            "code": "invalid_api_key"
        }
    }))

    mocker.patch("app.tools.search_tools.create_error_card_response", return_value=mocker.MagicMock())

    # --- Act ---
    await search_perplexity(mock_context, mock_memory, mock_settings, params)

    # --- Assert ---
    assert respx_mock.calls.call_count == 1

    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.search_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Error performing search via Perplexity" in args[1]
    assert "Status 401" in args[1]
    assert error_detail in args[1]


@pytest.mark.asyncio
async def test_search_perplexity_client_unavailable(
    mock_context: TurnContext,
    mock_memory: MemoryBase,
    mock_settings: Settings,
    mocker: MockerFixture,
):
    """Tests handling when the Perplexity client cannot be obtained."""
    params = SearchPerplexityParams(query="Search anything")
    mocker.patch("app.utils.api_clients.get_perplexity_client", side_effect=PerplexityClientError("API key not set in config"))
    mocker.patch("app.tools.search_tools.create_error_card_response", return_value=mocker.MagicMock())

    # --- Act ---
    await search_perplexity(mock_context, mock_memory, mock_settings, params)

    # --- Assert ---
    mock_context.send_activity.assert_called_once()
    error_card_mock = mocker.patch("app.tools.search_tools.create_error_card_response")
    error_card_mock.assert_called_once()
    args, _ = error_card_mock.call_args
    assert "Failed to initialize Perplexity client" in args[1]
    assert "API key not set in config" in args[1] 