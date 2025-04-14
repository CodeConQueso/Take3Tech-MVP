import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from github import Github, GithubException, UnknownObjectException
from github.Repository import Repository
from github.PullRequest import PullRequest
from github.Issue import Issue

# Modules involved
from tools import github_tools
from tools.schemas import GitHubCreatePRSchema, GitHubRequestCherryPickSchema
from memory.interface import MemoryInterface
from utils.config import Settings

# --- Fixtures ---

@pytest.fixture
def mock_pygithub_client(mocker):
    """Fixture providing a mocked PyGithub client."""
    mock_client = MagicMock(spec=Github)
    # Mock get_repo method
    mock_repo = MagicMock(spec=Repository)
    mock_repo.full_name = "mock/repo"
    mock_client.get_repo = MagicMock(return_value=mock_repo)
    # Mock create_pull method on the repo
    mock_pr = MagicMock(spec=PullRequest)
    mock_pr.html_url = "https://github.com/mock/repo/pull/1"
    mock_repo.create_pull = MagicMock(return_value=mock_pr)
    # Mock create_issue method on the repo
    mock_issue = MagicMock(spec=Issue)
    mock_issue.html_url = "https://github.com/mock/repo/issues/2"
    mock_repo.create_issue = MagicMock(return_value=mock_issue)
    
    # Patch the client getter in api_clients used by the tools
    mocker.patch('utils.api_clients.get_github_client', return_value=mock_client)
    # Patch the safe wrapper as well, if used directly
    mocker.patch('utils.api_clients.get_repo_safely', new_callable=AsyncMock, return_value=mock_repo)
    return mock_client, mock_repo

@pytest.fixture
def mock_memory_github(mocker):
    """Fixture providing a mocked MemoryInterface for GitHub tools."""
    memory = AsyncMock(spec=MemoryInterface)
    memory.store_user_value = AsyncMock()
    # Add other memory methods if needed by GitHub tools
    return memory

@pytest.fixture
def mock_settings_github(mocker):
    """Fixture providing mocked Settings, potentially with defaults."""
    mock = MagicMock(spec=Settings)
    mock.DEFAULT_GITHUB_REPO = "default/repo"
    # Patch settings where it's imported in the tools module
    mocker.patch('tools.github_tools.settings', mock)
    return mock

# --- Test Cases ---

@pytest.mark.asyncio
async def test_github_create_pr_success(mock_pygithub_client, mock_memory_github, mock_settings_github):
    """Test successful PR creation."""
    mock_client, mock_repo = mock_pygithub_client
    params_dict = {
        "repository": "test/repo",
        "title": "My New PR",
        "body": "PR description",
        "head_branch": "feature/abc",
        "base_branch": "develop",
        "draft": False
    }
    params = GitHubCreatePRSchema(**params_dict)
    user_id = "gh_user_1"

    result = await github_tools.github_create_pr(params, mock_memory_github, user_id, "Test User")

    assert result["success"] is True
    assert result["pr_url"] == "https://github.com/mock/repo/pull/1"
    # Check get_repo was called with the correct repo name
    mock_client.get_repo.assert_called_once_with(params.repository)
    # Check create_pull was called on the repo object with correct args
    mock_repo.create_pull.assert_called_once_with(
        title=params.title,
        body=params.body,
        head=params.head_branch,
        base=params.base_branch,
        draft=params.draft,
        maintainer_can_modify=True # Check default
    )
    # Check memory storage
    mock_memory_github.store_user_value.assert_any_call(user_id, "last_github_repo", params.repository)
    # Check storage of PR details (structure depends on implementation)
    mock_memory_github.store_user_value.assert_any_call(user_id, "last_pr_details", mocker.ANY)

@pytest.mark.asyncio
async def test_github_create_pr_uses_default_repo(mock_pygithub_client, mock_memory_github, mock_settings_github):
    """Test PR creation uses default repo if not specified."""
    mock_client, mock_repo = mock_pygithub_client
    params_dict = {
        # repository is missing
        "title": "Default Repo PR",
        "head_branch": "hotfix/123",
        "base_branch": "main"
    }
    params = GitHubCreatePRSchema(**params_dict)
    user_id = "gh_user_2"
    default_repo_name = mock_settings_github.DEFAULT_GITHUB_REPO

    result = await github_tools.github_create_pr(params, mock_memory_github, user_id, "Test User")

    assert result["success"] is True
    # Check get_repo was called with the default repo name
    mock_client.get_repo.assert_called_once_with(default_repo_name)
    mock_repo.create_pull.assert_called_once()
    mock_memory_github.store_user_value.assert_any_call(user_id, "last_github_repo", default_repo_name)

@pytest.mark.asyncio
async def test_github_create_pr_no_default_repo(mock_pygithub_client, mock_memory_github, mock_settings_github):
    """Test PR creation fails if repo not specified and no default exists."""
    mock_client, mock_repo = mock_pygithub_client
    mock_settings_github.DEFAULT_GITHUB_REPO = None # No default configured
    params_dict = {
        "title": "No Repo PR",
        "head_branch": "task/456",
        "base_branch": "main"
    }
    params = GitHubCreatePRSchema(**params_dict)
    user_id = "gh_user_3"

    result = await github_tools.github_create_pr(params, mock_memory_github, user_id, "Test User")

    assert result["success"] is False
    assert "Repository not specified" in result["error"]
    mock_client.get_repo.assert_not_called()
    mock_repo.create_pull.assert_not_called()

@pytest.mark.asyncio
async def test_github_create_pr_repo_not_found(mock_pygithub_client, mock_memory_github, mock_settings_github):
    """Test PR creation when the specified repository is not found (404)."""
    mock_client, mock_repo = mock_pygithub_client
    repo_name = "non/existent"
    params_dict = {"repository": repo_name, "title": "404 PR", "head_branch": "f", "base_branch": "b"}
    params = GitHubCreatePRSchema(**params_dict)
    user_id = "gh_user_4"

    # Mock get_repo to raise UnknownObjectException (simulates 404)
    mock_client.get_repo.side_effect = UnknownObjectException(status=404, data={"message": "Not Found"}, headers={})

    result = await github_tools.github_create_pr(params, mock_memory_github, user_id, "Test User")

    assert result["success"] is False
    assert "Repository not found" in result["error"]
    assert repo_name in result["error"]
    mock_client.get_repo.assert_called_once_with(repo_name)
    mock_repo.create_pull.assert_not_called()

@pytest.mark.asyncio
async def test_github_create_pr_github_api_error(mock_pygithub_client, mock_memory_github, mock_settings_github):
    """Test PR creation when the create_pull call fails."""
    mock_client, mock_repo = mock_pygithub_client
    params_dict = {"repository": "test/repo", "title": "Fail PR", "head_branch": "f", "base_branch": "b"}
    params = GitHubCreatePRSchema(**params_dict)
    user_id = "gh_user_5"
    error_message = "Validation Failed"

    # Mock create_pull to raise GithubException
    mock_repo.create_pull.side_effect = GithubException(status=422, data={"message": error_message}, headers={})

    result = await github_tools.github_create_pr(params, mock_memory_github, user_id, "Test User")

    assert result["success"] is False
    assert "GitHub API error creating PR" in result["error"]
    assert error_message in result["error"]
    mock_client.get_repo.assert_called_once_with(params.repository)
    mock_repo.create_pull.assert_called_once()

@pytest.mark.asyncio
async def test_github_create_pr_client_unavailable(mock_memory_github, mock_settings_github, mocker):
    """Test PR creation when GitHub client is not available."""
    # Patch the client getter to return None
    mocker.patch('utils.api_clients.get_github_client', return_value=None)
    params_dict = {"repository": "test/repo", "title": "No Client PR", "head_branch": "f", "base_branch": "b"}
    params = GitHubCreatePRSchema(**params_dict)
    user_id = "gh_user_6"

    result = await github_tools.github_create_pr(params, mock_memory_github, user_id, "Test User")

    assert result["success"] is False
    assert "GitHub client is not available" in result["error"]

# --- Tests for github_request_cherry_pick (to be added) ---


@pytest.mark.asyncio
async def test_placeholder_github_tools(): # Keep placeholder or remove later
    assert True 