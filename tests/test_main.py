import pytest
import pytest_asyncio
from httpx import AsyncClient
from fastapi import status
from pytest_mock import MockerFixture

# Assuming your FastAPI app instance is named 'app' in 'app.main'
# Adjust the import path if necessary
from app.main import app
# Assuming the adapter is accessible globally or via app state
# from app.bot import adapter # Or wherever ADAPTER is defined

# Fixture for the test client
@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

# --- Test /api/messages ---

@pytest.mark.asyncio
async def test_messages_endpoint(client: AsyncClient, mocker: MockerFixture):
    """Tests the POST /api/messages endpoint."""
    # Mock the adapter's process_activity method
    # Adjust the target path according to where your adapter instance is defined
    mock_process_activity = mocker.patch("app.bot.ADAPTER.process_activity", new_callable=mocker.AsyncMock)

    # Example activity payload (minimal)
    activity_payload = {
        "type": "message",
        "text": "hello bot",
        "from": {"id": "user1"},
        "recipient": {"id": "bot1"},
        "conversation": {"id": "conv1"},
        "channelId": "msteams",
        "serviceUrl": "http://test.service.url"
    }

    headers = {"Authorization": "Bearer test_token"} # Add auth if needed by your endpoint

    response = await client.post("/api/messages", json=activity_payload, headers=headers)

    # Assert the response status code (usually 200 or 202 Accepted)
    # Check your BotFrameworkAdapter implementation details
    assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_202_ACCEPTED]

    # Assert that process_activity was called once
    mock_process_activity.assert_awaited_once()

    # Check arguments passed to process_activity
    call_args, call_kwargs = mock_process_activity.call_args
    # Arg 1 should be the auth header (or similar)
    # Check your adapter's invoke method signature
    # assert call_args[0] == headers["Authorization"]
    # Arg 2 should be the activity dict
    assert call_args[1] == activity_payload
    # Arg 3 should be the bot's on_turn handler (or similar)
    # assert callable(call_args[2])

# --- Test /health ---

@pytest.mark.asyncio
async def test_health_endpoint_all_healthy(client: AsyncClient, mocker: MockerFixture):
    """Tests the /health endpoint when all components are healthy."""
    # Mock the individual health check functions/methods
    # Adjust paths as needed
    mocker.patch("app.memory.redis_memory.RedisMemory.health_check", return_value=True)
    mocker.patch("app.llm.health_check", return_value=True)
    # mocker.patch("app.scheduler.health_check", return_value=True) # If you have one

    response = await client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"
    assert data["components"]["memory"] == "healthy"
    assert data["components"]["llm"] == "healthy"
    # assert data["components"]["scheduler"] == "healthy"

@pytest.mark.asyncio
async def test_health_endpoint_one_unhealthy(client: AsyncClient, mocker: MockerFixture):
    """Tests the /health endpoint when one component is unhealthy."""
    # Mock the individual health check functions/methods
    mocker.patch("app.memory.redis_memory.RedisMemory.health_check", return_value=False) # Memory fails
    mocker.patch("app.llm.health_check", return_value=True)

    response = await client.get("/health")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["components"]["memory"] == "unhealthy"
    assert data["components"]["llm"] == "healthy" 