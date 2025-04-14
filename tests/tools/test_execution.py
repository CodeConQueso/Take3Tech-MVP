import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import ValidationError

# Modules involved in testing
from tools import execution
from tools import schemas # Import schemas for validation testing
from tools import general_tools, github_tools, jira_tools, octopus_tools, search_tools # Import tool modules to patch
from memory.interface import MemoryInterface

# --- Fixtures ---

@pytest.fixture
def mock_memory_tools(mocker):
    """Fixture providing a mocked MemoryInterface for tool execution."""
    memory = AsyncMock(spec=MemoryInterface)
    # Add default return values for methods tools might call
    memory.get_user_data = AsyncMock(return_value={})
    memory.store_user_value = AsyncMock()
    return memory

@pytest_asyncio.fixture
async def mock_tool_functions(mocker):
    """Fixture to mock all individual tool functions."""
    mocks = {
        # General
        "manage_preferences": mocker.patch('tools.general_tools.manage_preferences', new_callable=AsyncMock, return_value={"success": True, "message": "Prefs mocked"}),
        "manage_aliases": mocker.patch('tools.general_tools.manage_aliases', new_callable=AsyncMock, return_value={"success": True, "message": "Alias mocked"}),
        "schedule_reminder": mocker.patch('tools.general_tools.schedule_reminder', new_callable=AsyncMock, return_value={"success": True, "message": "Reminder mocked"}),
        "suggest_next_actions": mocker.patch('tools.general_tools.suggest_next_actions', new_callable=AsyncMock, return_value={"success": True, "suggestions": []}),
        "explain_item": mocker.patch('tools.general_tools.explain_item', new_callable=AsyncMock, return_value={"success": True, "explanation": "Mock explanation"}),
        # GitHub
        "github_create_pr": mocker.patch('tools.github_tools.github_create_pr', new_callable=AsyncMock, return_value={"success": True, "pr_url": "mock_pr_url"}),
        "github_request_cherry_pick": mocker.patch('tools.github_tools.github_request_cherry_pick', new_callable=AsyncMock, return_value={"success": True, "issue_url": "mock_issue_url"}),
        "github_pr_summary": mocker.patch('tools.github_tools.github_pr_summary', new_callable=AsyncMock, return_value={"success": True, "summary": "Mock summary"}),
        # Jira
        "jira_create_issue": mocker.patch('tools.jira_tools.jira_create_issue', new_callable=AsyncMock, return_value={"success": True, "issue_key": "MOCK-1", "issue_url": "mock_jira_url"}),
        "jira_update_status": mocker.patch('tools.jira_tools.jira_update_status', new_callable=AsyncMock, return_value={"success": True, "message": "Status updated"}),
        "jira_summarize_feedback": mocker.patch('tools.jira_tools.jira_summarize_feedback', new_callable=AsyncMock, return_value={"success": True, "summary": "Feedback summary"}),
        # Octopus
        "octopus_trigger_deployment": mocker.patch('tools.octopus_tools.octopus_trigger_deployment', new_callable=AsyncMock, return_value={"success": True, "deployment_url": "mock_deploy_url"}),
        # Search
        "search_perplexity": mocker.patch('tools.search_tools.search_perplexity', new_callable=AsyncMock, return_value={"success": True, "results": []}),
    }
    return mocks

# --- Test Cases ---

@pytest.mark.asyncio
async def test_execute_tool_success(mock_tool_functions, mock_memory_tools):
    """Test successful execution of a known tool with valid parameters."""
    tool_name = "github_create_pr"
    tool_params = {"repository": "org/repo", "title": "Test PR", "head_branch": "feature", "base_branch": "main"}
    user_id = "test_user"
    user_name = "Test User Name"

    # Get the mock for the specific tool function
    mock_func = mock_tool_functions[tool_name]

    result = await execution.execute_tool(
        tool_name=tool_name,
        tool_params=tool_params,
        memory=mock_memory_tools,
        user_id=user_id,
        user_name=user_name
    )

    # Check the result from the mocked function is returned
    assert result["success"] is True
    assert result["pr_url"] == "mock_pr_url"

    # Verify the mocked function was called with validated params and context
    # Pydantic model validation happens internally, so we check the final call
    # The schema adds default values if not provided, so check based on schema
    expected_call_params = schemas.GitHubCreatePRSchema(**tool_params)

    mock_func.assert_awaited_once_with(
        params=expected_call_params,
        memory=mock_memory_tools,
        user_id=user_id,
        user_name=user_name,
        adapter=None, # Check defaults if not passed
        conversation_ref=None
    )

@pytest.mark.asyncio
async def test_execute_tool_unknown_tool(mock_memory_tools):
    """Test calling a tool name that is not registered."""
    tool_name = "non_existent_tool"
    tool_params = {}
    user_id = "test_user"

    result = await execution.execute_tool(
        tool_name=tool_name,
        tool_params=tool_params,
        memory=mock_memory_tools,
        user_id=user_id,
        user_name="Test"
    )

    assert result["success"] is False
    assert "Unknown tool" in result["error"]
    assert tool_name in result["error"]

@pytest.mark.asyncio
async def test_execute_tool_validation_error(mock_memory_tools):
    """Test calling a tool with parameters that fail Pydantic validation."""
    tool_name = "github_create_pr" # Requires 'repository', 'title', etc.
    tool_params = {"repository": "org/repo"} # Missing required fields
    user_id = "test_user"

    result = await execution.execute_tool(
        tool_name=tool_name,
        tool_params=tool_params,
        memory=mock_memory_tools,
        user_id=user_id,
        user_name="Test"
    )

    assert result["success"] is False
    assert "Validation Error" in result["error"]
    # Check that the error message indicates missing fields like 'title'
    assert "'title'" in result["error"]
    assert "field required" in result["error"]

@pytest.mark.asyncio
async def test_execute_tool_function_raises_exception(mock_tool_functions, mock_memory_tools):
    """Test when the underlying tool function raises an unexpected exception."""
    tool_name = "jira_create_issue"
    tool_params = {"project_key": "PROJ", "summary": "Bug report", "issue_type": "Bug"}
    user_id = "test_user"
    error_message = "Jira API connection failed"

    # Configure the mock tool function to raise an error
    mock_func = mock_tool_functions[tool_name]
    mock_func.side_effect = Exception(error_message)

    result = await execution.execute_tool(
        tool_name=tool_name,
        tool_params=tool_params,
        memory=mock_memory_tools,
        user_id=user_id,
        user_name="Test"
    )

    assert result["success"] is False
    assert "Error executing tool" in result["error"]
    assert tool_name in result["error"]
    assert error_message in result["error"]

@pytest.mark.asyncio
async def test_execute_tool_passes_context(mock_tool_functions, mock_memory_tools):
    """Test that context like adapter and conversation_ref are passed correctly."""
    tool_name = "manage_preferences"
    tool_params = {"action": "set", "key": "theme", "value": "blue"}
    user_id = "ctx_user"
    user_name = "Context User"
    # Create mock adapter and conv_ref
    mock_adapter = MagicMock()
    mock_conv_ref = MagicMock()

    mock_func = mock_tool_functions[tool_name]

    await execution.execute_tool(
        tool_name=tool_name,
        tool_params=tool_params,
        memory=mock_memory_tools,
        user_id=user_id,
        user_name=user_name,
        adapter=mock_adapter, # Pass the context objects
        conversation_ref=mock_conv_ref
    )

    expected_call_params = schemas.ManagePreferencesSchema(**tool_params)
    mock_func.assert_awaited_once_with(
        params=expected_call_params,
        memory=mock_memory_tools,
        user_id=user_id,
        user_name=user_name,
        adapter=mock_adapter, # Verify they were received
        conversation_ref=mock_conv_ref
    )

# Remove placeholder
# @pytest.mark.asyncio
# async def test_placeholder_execution():
#     assert True 