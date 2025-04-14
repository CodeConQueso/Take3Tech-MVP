import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Set, Optional

from botbuilder.core import TurnContext
from botbuilder.schema import Activity, ChannelAccount, ConversationAccount
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.group import Group
from msgraph.generated.users.item.get_member_groups.get_member_groups_response import GetMemberGroupsResponse
from azure.core.exceptions import ClientAuthenticationError

# Modules to test
from bot_core import _check_permissions, _get_user_groups, PermissionDeniedError, _get_graph_client
from utils.config import Settings # To mock settings

# --- Fixtures ---

@pytest.fixture
def mock_settings(mocker):
    """Fixture to provide mocked Settings object."""
    mock = Settings() # Create an instance to modify
    # Default to RBAC enabled with some groups for testing
    mocker.patch('bot_core.settings', mock)
    mock.RBAC_ENABLED = True
    mock.RBAC_CONFIG = {
        "deployments": {"group_deploy"},
        "admin": {"group_admin"}
    }
    # Mock Graph API credentials
    mock.MSGRAPH_TENANT_ID = "test_tenant"
    mock.MSGRAPH_CLIENT_ID = "test_client"
    mock.MSGRAPH_CLIENT_SECRET = "test_secret"
    return mock

@pytest.fixture
def mock_turn_context():
    """Fixture to create a mock TurnContext with a basic Activity."""
    activity = Activity(
        type="message",
        channel_id="msteams",
        service_url="https://test.com",
        from_property=ChannelAccount(id="user1", name="Test User", aad_object_id="user_aad_id_1"),
        recipient=ChannelAccount(id="bot1", name="Test Bot"),
        conversation=ConversationAccount(id="conv1"),
        text="test message"
    )
    context = TurnContext(MagicMock(), activity) # Adapter is mocked
    return context

@pytest_asyncio.fixture
async def mock_graph_client(mocker):
    """Fixture providing a mocked GraphServiceClient."""
    mock_client = AsyncMock()

    # Mock the post method for get_member_groups
    mock_response = GetMemberGroupsResponse()
    mock_response.value = ["group_deploy", "group_other"] # Default user groups
    mock_client.users.by_user_id().get_member_groups.post = AsyncMock(return_value=mock_response)

    # Patch _get_graph_client to return this mock client
    mocker.patch('bot_core._get_graph_client', return_value=mock_client)
    return mock_client


# --- Test Cases ---

# Tests for _get_user_groups

@pytest.mark.asyncio
async def test_get_user_groups_success(mock_settings, mock_graph_client):
    """Test successfully fetching user groups."""
    user_aad_id = "user_aad_id_1"
    expected_groups = {"group_deploy", "group_other"}
    
    # Ensure the mock client returns the expected groups
    mock_response = GetMemberGroupsResponse()
    mock_response.value = list(expected_groups)
    mock_graph_client.users.by_user_id(user_aad_id).get_member_groups.post.return_value = mock_response

    groups = await _get_user_groups(user_aad_id)
    
    assert groups == expected_groups
    mock_graph_client.users.by_user_id(user_aad_id).get_member_groups.post.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_user_groups_no_groups(mock_settings, mock_graph_client):
    """Test fetching when the user belongs to no groups."""
    user_aad_id = "user_aad_id_no_groups"
    
    # Mock response with empty value list
    mock_response = GetMemberGroupsResponse()
    mock_response.value = []
    mock_graph_client.users.by_user_id(user_aad_id).get_member_groups.post.return_value = mock_response

    groups = await _get_user_groups(user_aad_id)
    
    assert groups == set()
    mock_graph_client.users.by_user_id(user_aad_id).get_member_groups.post.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_user_groups_graph_api_error(mock_settings, mock_graph_client, mocker):
    """Test handling of ODataError from Graph API (after retries)."""
    user_aad_id = "user_aad_id_error"
    
    # Configure the mock Graph client to raise ODataError on the final attempt
    # The retry decorator is on _get_user_groups itself
    mock_graph_client.users.by_user_id(user_aad_id).get_member_groups.post.side_effect = ODataError("Graph API failed")

    # Patch the retry decorator to disable waiting for faster tests
    mocker.patch('bot_core.retry', lambda **kwargs: lambda f: f) # Basic mock, might need adjustment if complex retry logic is tested

    with pytest.raises(ODataError):
        await _get_user_groups(user_aad_id)
        
    # Check it was called (at least once, retry logic is mocked out here)
    assert mock_graph_client.users.by_user_id(user_aad_id).get_member_groups.post.call_count >= 1


@pytest.mark.asyncio
async def test_get_user_groups_graph_client_unavailable(mock_settings, mocker):
    """Test behavior when _get_graph_client returns None (failed init)."""
    user_aad_id = "user_aad_id_client_fail"
    
    # Patch _get_graph_client to return None directly
    mocker.patch('bot_core._get_graph_client', return_value=None)

    with pytest.raises(Exception, match="Graph client unavailable"):
        await _get_user_groups(user_aad_id)
        
    # We don't expect the graph client's methods to be called in this case

# Placeholder for other tests
@pytest.mark.asyncio
async def test_placeholder(): # Keep one placeholder or remove later
    assert True

# Add tests for _check_permissions here

# Placeholder for tests to be added
@pytest.mark.asyncio
async def test_placeholder():
    assert True

# Add tests for _get_user_groups and _check_permissions here 

# --- Tests for _check_permissions ---

@pytest.mark.asyncio
async def test_check_permissions_rbac_disabled(mock_settings, mock_turn_context, mocker):
    """Test that permission is granted when RBAC is disabled."""
    mock_settings.RBAC_ENABLED = False
    # Patch audit log to check it was called correctly
    mock_audit_log = mocker.patch('bot_core.audit.log')

    tool_name = "octopus_trigger_deployment"
    
    await _check_permissions(mock_turn_context, tool_name)
    
    mock_audit_log.assert_called_once_with(
        "permission_check", 
        user_id="user_aad_id_1", 
        tool=tool_name, 
        rbac_enabled=False, 
        allowed=True
    )
    # No exception should be raised

@pytest.mark.asyncio
async def test_check_permissions_rbac_enabled_allowed(mock_settings, mock_turn_context, mock_graph_client, mocker):
    """Test permission granted when user is in the required group."""
    # mock_graph_client fixture sets user groups to {"group_deploy", "group_other"}
    # mock_settings sets RBAC_CONFIG["deployments"] = {"group_deploy"}
    mock_audit_log = mocker.patch('bot_core.audit.log')
    tool_name = "octopus_trigger_deployment" # Requires 'deployments' group
    user_aad_id = "user_aad_id_1"
    
    # Patch the actual _get_user_groups call within _check_permissions to avoid re-triggering graph mock setup
    # Let it return the groups set by the mock_graph_client fixture
    mocker.patch('bot_core._get_user_groups', return_value={"group_deploy", "group_other"})

    await _check_permissions(mock_turn_context, tool_name)
    
    # Verify audit log includes user/required groups
    mock_audit_log.assert_called_with(
        "permission_check", 
        user_id=user_aad_id, 
        tool=tool_name, 
        rbac_enabled=True, 
        allowed=True, 
        user_groups=list({"group_deploy", "group_other"}), # Order might vary, check content
        required_groups=list({"group_deploy"})
    )
    # No exception expected

@pytest.mark.asyncio
async def test_check_permissions_rbac_enabled_denied(mock_settings, mock_turn_context, mock_graph_client, mocker):
    """Test permission denied when user is not in the required group."""
    mock_audit_log = mocker.patch('bot_core.audit.log')
    tool_name = "admin_tool" # Assumes this requires 'admin' group
    mock_settings.RBAC_CONFIG["admin_tool"] = {"group_admin"} # Define requirement
    user_aad_id = "user_aad_id_1"
    user_groups = {"group_deploy", "group_other"}

    mocker.patch('bot_core._get_user_groups', return_value=user_groups)

    with pytest.raises(PermissionDeniedError, match="Required group membership is missing"):
        await _check_permissions(mock_turn_context, tool_name)
    
    mock_audit_log.assert_called_with(
        "permission_check", 
        user_id=user_aad_id, 
        tool=tool_name, 
        rbac_enabled=True, 
        allowed=False, 
        user_groups=list(user_groups), 
        required_groups=list({"group_admin"})
    )

@pytest.mark.asyncio
async def test_check_permissions_no_specific_group_required(mock_settings, mock_turn_context, mock_graph_client, mocker):
    """Test permission granted when tool has no specific RBAC group defined."""
    mock_audit_log = mocker.patch('bot_core.audit.log')
    tool_name = "tool_without_rbac_config" # Not in RBAC_CONFIG
    user_aad_id = "user_aad_id_1"
    user_groups = {"group_deploy", "group_other"}

    mocker.patch('bot_core._get_user_groups', return_value=user_groups)

    await _check_permissions(mock_turn_context, tool_name)
    
    mock_audit_log.assert_called_with(
        "permission_check", 
        user_id=user_aad_id, 
        tool=tool_name, 
        rbac_enabled=True, 
        allowed=True, 
        reason="No specific group required for this tool"
    )
    # No exception expected

@pytest.mark.asyncio
async def test_check_permissions_missing_aad_id(mock_settings, mock_turn_context, mocker):
    """Test permission denied when user AAD ID is missing in activity."""
    mock_audit_log = mocker.patch('bot_core.audit.log')
    tool_name = "octopus_trigger_deployment"
    
    # Modify context to remove AAD ID
    mock_turn_context.activity.from_property.aad_object_id = None
    # Set a non-AAD ID for logging
    mock_turn_context.activity.from_property.id = "user_teams_id_only"

    with pytest.raises(PermissionDeniedError, match="Cannot verify identity"):
        await _check_permissions(mock_turn_context, tool_name)
    
    mock_audit_log.assert_called_once_with(
        "permission_check", 
        user_id="user_teams_id_only", 
        tool=tool_name, 
        rbac_enabled=True, 
        allowed=False, 
        reason="Missing AAD Object ID"
    )

@pytest.mark.asyncio
async def test_check_permissions_get_groups_fails(mock_settings, mock_turn_context, mocker):
    """Test permission denied when _get_user_groups raises an exception."""
    mock_audit_log = mocker.patch('bot_core.audit.log')
    tool_name = "octopus_trigger_deployment"
    user_aad_id = "user_aad_id_1"
    error_message = "Failed to get groups after retries"

    # Patch _get_user_groups to raise an exception
    mocker.patch('bot_core._get_user_groups', side_effect=Exception(error_message))

    with pytest.raises(PermissionDeniedError, match="Error checking permissions after multiple attempts"):
        await _check_permissions(mock_turn_context, tool_name)
        
    mock_audit_log.assert_called_once_with(
        "permission_check", 
        user_id=user_aad_id, 
        tool=tool_name, 
        rbac_enabled=True, 
        allowed=False, 
        reason=f"Failed to get user groups after retries: {error_message}"
    ) 