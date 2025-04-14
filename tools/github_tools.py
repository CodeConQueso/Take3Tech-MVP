import logging
import re
from typing import Dict, Any, Tuple, Optional, List
from github import GithubException, NamedUser, Github, UnknownObjectException # Import Github for type hint
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from github.PullRequest import PullRequest
from github.Issue import Issue # Added for issue creation return type

from .schemas import PrSummaryParams, CherryPickParams
from memory.interface import MemoryInterface
from utils.api_clients import get_github_client, get_repo_safely, retry_transient # Import retry decorator
from utils.config import settings
from utils.audit_logger import audit

logger = logging.getLogger(__name__)

@retry_transient
async def _fetch_pr_details(github_repo: Any, pr_number: int) -> Dict[str, Any]:
     """Internal helper to fetch and serialize PR details, wrapped in retry."""
     pr = github_repo.get_pull(pr_number)
     # Serialize data needed for card/explanation
     return {
         "success": True, # Indicate fetch success
         "repo_name": github_repo.full_name,
         "pr_number": pr.number,
         "title": pr.title,
         "state": pr.state,
         "draft": pr.draft,
         "merged": pr.merged,
         "html_url": pr.html_url,
         "user_login": pr.user.login,
         "head_ref": pr.head.ref,
         "base_ref": pr.base.ref,
         "created_at": pr.created_at.isoformat() if pr.created_at else None,
         "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
         "assignees": [a.login for a in pr.assignees],
         "reviewers": [r.login for r in pr.requested_reviewers],
         "commits": pr.commits,
         "additions": pr.additions,
         "deletions": pr.deletions,
         "changed_files": pr.changed_files,
         "body": pr.body or "No description provided."
     }

async def get_pr_summary(
    repo_name: Optional[str],
    pr_number: int,
    user_id: str,
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Fetches details and summarizes a specific GitHub Pull Request.

    Resolves the repository name using the input parameter, user memory,
    or the default setting.
    Calls the GitHub API to get PR details (title, author, status, body, etc.).

    Args:
        repo_name: The repository name ('owner/repo') or None to use memory/default.
        pr_number: The pull request number.
        user_id: The ID of the user requesting the summary.
        memory: Memory interface for resolving default repository.

    Returns:
        Dict: Success dictionary with PR details or error dictionary.
    """
    github: Optional[Github] = get_github_client()
    if not github: return {"error": "GitHub client not configured."}

    if not repo_name: return {"error": "Repository name is missing.", "suggestion": "Specify repository (owner/repo) or set default."}
    if not pr_number: return {"error": "Pull Request number missing."}
    if not re.match(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+$", repo_name): return {"error": f"Invalid repository format: '{repo_name}'.", "suggestion": "Use 'owner/repo'."}

    logger.info(f"Executing tool: get_pr_summary for {repo_name}#{pr_number}")
    try:
        repo = await get_repo_safely(repo_name) # Uses retry wrapper
        pr_details = await _fetch_pr_details(repo, pr_number) # Uses retry wrapper

        await memory.set_user_data(user_id, "last_github_repo", repo_name)
        await memory.set_user_data(user_id, f"last_pr_details", {"repo": repo_name, "number": pr_number, "title": pr_details.get('title')})

        return pr_details # Return the fetched details dict

    except GithubException as e:
        logger.error(f"GitHub API error fetching PR {repo_name}#{pr_number}: {e.status} - {e.data}")
        error_message = f"Could not access PR #{pr_number} in '{repo_name}'."
        suggestion = "Check repo/PR number and bot permissions."
        status_code = e.status
        if status_code == 404: suggestion += " Item not found."
        elif status_code in [401, 403]: suggestion = "Check GITHUB_TOKEN validity and access rights."
        return {"error": error_message, "suggestion": suggestion, "status_code": status_code}
    except Exception as e:
        logger.exception(f"Unexpected error in get_pr_summary for {repo_name}#{pr_number}: {e}")
        return {"error": "An unexpected error occurred fetching PR summary.", "suggestion": "Check bot logs."}


@retry_transient
async def validate_cherry_pick_source(github_repo: Any, source_ref: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Internal: Validates source ref (PR/commit) and returns SHA, desc, URL, error. Retries transients."""
    # (Logic remains the same as previous version, benefits from retry on API calls)
    # ...
    commit_sha = None; source_description = ""; source_url = None; error = None
    try:
        if source_ref.isdigit():
            pr = github_repo.get_pull(int(source_ref))
            commit_sha = pr.merge_commit_sha if pr.merged and pr.merge_commit_sha else pr.head.sha
            source_description = f"Pull Request #{pr.number} ('{pr.title}')"
            source_url = pr.html_url
            if not commit_sha: error = f"Could not determine commit SHA for PR #{pr.number}."
        elif len(source_ref) >= 7:
             commit = github_repo.get_commit(source_ref)
             commit_sha = commit.sha
             source_description = f"Commit `{commit_sha[:7]}`"
             source_url = commit.html_url
        else: error = f"Invalid source reference: '{source_ref}'."
    except GithubException as e:
         status = e.status
         if status == 404 or status == 422: error = f"Source '{source_ref}' not found or invalid in {github_repo.full_name}."
         else: logger.error(f"GH API Error validating source {source_ref}: {e.status}"); raise # Re-raise other errors for retry
    except Exception as e: logger.exception(f"Unexpected validation error: {e}"); error = f"Unexpected validation error: {e}"
    return commit_sha, source_description, source_url, error


async def prepare_cherry_pick_request(
    target_branch: str,
    pr_number: int,
    repo_name: Optional[str],
    user_id: str,
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    (Internal/Helper) Validates information for a cherry-pick request.

    This tool primarily fetches details about the source PR and target branch
    to ensure they exist before potentially creating a Jira issue or GitHub PR.
    It doesn't perform the cherry-pick itself.

    Args:
        target_branch: The name of the target branch.
        pr_number: The source pull request number.
        repo_name: The repository name ('owner/repo') or None to use memory/default.
        user_id: The ID of the user making the request.
        memory: Memory interface for resolving default repository.

    Returns:
        Dict: Success dictionary with validated details or error dictionary.
    """
    github: Optional[Github] = get_github_client()
    if not github: return {"error": "GitHub client not configured."}

    if not repo_name: return {"error": "Repository name is missing.", "suggestion": "Specify repository (owner/repo) or set default."}
    if not pr_number: return {"error": "Pull Request number missing."}
    if not re.match(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9._-]+$", repo_name): return {"error": f"Invalid repository format: '{repo_name}'.", "suggestion": "Use 'owner/repo'."}

    logger.info(f"Executing tool: prepare_cherry_pick_request for {repo_name}#{pr_number}")
    try:
        repo = await get_repo_safely(repo_name)
        commit_sha, source_desc, source_url, validation_error = await validate_cherry_pick_source(repo, pr_number)
        if validation_error: return {"error": f"Source validation failed: {validation_error}"}
        if not commit_sha: return {"error": "Could not determine commit SHA from source reference."}

        # Validate target branch exists (wrapped in retry)
        @retry_transient
        async def _check_branch():
            try: repo.get_branch(target_branch)
            except GithubException as e:
                if e.status == 404: return False # Found not exists
                else: raise # Re-raise other errors
            return True

        branch_exists = await _check_branch()
        if not branch_exists:
             return {"error": f"Target branch '{target_branch}' not found in {repo_name}.", "suggestion": "Verify branch name."}

        await memory.set_user_data(user_id, "last_github_repo", repo_name)
        return {"success": True, "repo_name": repo_name, "target_branch": target_branch, "source_ref": pr_number, "commit_sha": commit_sha, "source_description": source_desc, "source_url": source_url, "jira_project_key": kwargs.get('jira_project_key')}

    except GithubException as e:
        logger.error(f"GitHub API error during cherry-pick prep: {e.status} - {e.data}")
        return {"error": "GitHub API Error during preparation.", "suggestion": f"Status {e.status}"}
    except Exception as e:
        logger.exception(f"Unexpected error in prepare_cherry_pick_request: {e}")
        return {"error": "An unexpected error occurred during cherry-pick preparation."}


@retry_transient
async def get_assigned_prs_internal(github_client_instance: Github, github_login: str, max_results: int) -> List[Dict]:
    """Internal fetch logic with retry for assigned PRs."""
    prs_found = []
    query = f"is:pr is:open assignee:{github_login}"
    result = github_client_instance.search_issues(query, sort="updated", order="desc")
    count = 0
    for issue in result:
        if count >= max_results: break
        try:
            # Basic details from issue search result are often enough for digest
            prs_found.append({
                "title": issue.title, "url": issue.html_url, "repo": issue.repository.full_name,
                "number": issue.number, "state": issue.state,
                "updated_at": issue.updated_at.isoformat() if issue.updated_at else None
            })
            count += 1
        except Exception as pr_err:
            logger.warning(f"Error processing issue# {issue.number} for digest: {pr_err}")
            continue # Skip this PR if basic processing fails
    return prs_found

async def get_assigned_prs(
    user_github_login: str,
    user_id: str, # For logging
    memory: MemoryInterface, # Unused but kept for consistency
    **kwargs
) -> Dict[str, Any]:
    """
    (Internal) Fetches open pull requests assigned to a specific GitHub user.

    Used primarily by the daily digest feature.
    Queries the GitHub API for open PRs assigned to the provided login.

    Args:
        user_github_login: The GitHub username to search for assigned PRs.
        user_id: The internal ID of the user (for logging).
        memory: Memory interface (unused).

    Returns:
        Dict: Success dictionary with a list of assigned PRs or error dictionary.
    """
    github: Optional[Github] = get_github_client()
    if not github: return {"error": "GitHub client not configured."}
    logger.info(f"Fetching assigned PRs for GitHub login: {user_github_login} (User ID: {user_id})")
    try:
        prs = await get_assigned_prs_internal(github, user_github_login, settings.SUGGEST_MAX_PRS_TO_CHECK)
        logger.info(f"Found {len(prs)} open assigned PRs for {user_github_login}.")
        return {"success": True, "assigned_prs": prs}
    except GithubException as e:
        logger.error(f"GitHub API error searching assigned PRs for {user_github_login}: {e.status} - {e.data}")
        return {"error": "GitHub API Error searching PRs.", "suggestion": f"Status: {e.status}"}
    except Exception as e:
        logger.exception(f"Unexpected error searching assigned PRs for {user_github_login}: {e}")
        return {"error": "Unexpected error searching GitHub PRs."}

async def github_create_pr(
    repo_name: Optional[str],
    base_branch: str,
    head_branch: str,
    title: str,
    body: str,
    user_id: str,
    user_name: str, # For context/mention
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Creates a new GitHub Pull Request.

    Args:
        repo_name: Repository name ('owner/repo') or None to use memory/default.
        base_branch: The target branch for the PR (e.g., 'main').
        head_branch: The source branch containing changes.
        title: The title for the PR.
        body: The description body for the PR.
        user_id: ID of the user initiating the action.
        user_name: Name of the user initiating the action.
        memory: Memory interface for resolving default repository.

    Returns:
        Dict: Success dictionary with details of the created PR or error dictionary.
    """
    github: Optional[Github] = get_github_client()
    if not github: return {"error": "GitHub client not configured."}
    logger.info(f"Attempting to create PR in {repo_name} from {head_branch} to {base_branch}. User: {user_id}")
    audit.log("tool_attempt", user_id=user_id, tool_name="github_create_pr", params=locals()) # Log attempt

    try:
        repo = github.get_repo(repo_name)
    except UnknownObjectException:
        error_msg = f"Repository '{repo_name}' not found or access denied."
        logger.error(f"{error_msg} User: {user_id}")
        audit.log("tool_failure", user_id=user_id, tool_name="github_create_pr", error=error_msg)
        return {"success": False, "error": error_msg, "suggestion": "Verify repository name and bot permissions."}
    except GithubException as e:
        error_msg = f"GitHub API error accessing repository '{repo_name}': {e.status} {e.data.get('message', 'Unknown error')}"
        logger.error(f"{error_msg} User: {user_id}", exc_info=True)
        audit.log("tool_failure", user_id=user_id, tool_name="github_create_pr", error=error_msg, details=str(e.data))
        return {"success": False, "error": error_msg, "suggestion": "Check GitHub API status or bot permissions."}
    except Exception as e:
        error_msg = f"Unexpected error getting repository '{repo_name}': {e}"
        logger.exception(f"{error_msg} User: {user_id}")
        audit.log("tool_failure", user_id=user_id, tool_name="github_create_pr", error=error_msg)
        return {"success": False, "error": error_msg, "suggestion": "Investigate logs for details."}

    try:
        # Ensure branches exist (optional, create_pull might handle this)
        # repo.get_branch(base_branch)
        # repo.get_branch(head_branch)

        # Create the pull request
        pull_request: PullRequest = repo.create_pull(
            title=title,
            body=body,
            base=base_branch,
            head=head_branch,
            maintainer_can_modify=True, # Default, allows maintainers to modify the PR
            draft=False # Create as a ready-for-review PR
        )

        success_msg = f"Successfully created Pull Request #{pull_request.number} in {repo_name}."
        logger.info(f"{success_msg} URL: {pull_request.html_url}. User: {user_id}")
        audit.log("tool_success", user_id=user_id, tool_name="github_create_pr", pr_number=pull_request.number, pr_url=pull_request.html_url)
        return {
            "success": True,
            "pr_number": pull_request.number,
            "pr_url": pull_request.html_url,
            "message": success_msg
        }

    except GithubException as e:
        # Handle common PR creation errors
        error_detail = e.data.get('message', 'Unknown GitHub error')
        suggestion = "Check branches, permissions, and potential existing PRs."
        if e.status == 422: # Unprocessable Entity - common for validation errors
            errors = e.data.get("errors", [])
            error_messages = [err.get("message", "validation issue") for err in errors]
            error_detail = f"Validation failed: {'; '.join(error_messages)}"
            if any("A pull request already exists" in msg for msg in error_messages):
                 suggestion = f"A pull request already exists for {head_branch} -> {base_branch}."
            elif any("No commits between" in msg for msg in error_messages):
                 suggestion = f"There are no commits difference between {base_branch} and {head_branch}."
        elif e.status == 403: # Forbidden
            error_detail = "Permission denied to create pull request."
            suggestion = "Ensure the bot has write access to the repository."
        elif e.status == 404: # Not Found
            error_detail = "Base or head branch not found."
            suggestion = f"Verify that branches '{base_branch}' and '{head_branch}' exist."

        logger.error(f"GitHub API error creating PR in {repo_name}: {e.status} {error_detail}. User: {user_id}", exc_info=False)
        audit.log("tool_failure", user_id=user_id, tool_name="github_create_pr", error=f"{e.status} {error_detail}", details=str(e.data))
        return {"success": False, "error": error_detail, "suggestion": suggestion}

    except Exception as e:
        error_msg = f"Unexpected error creating PR in {repo_name}: {e}"
        logger.exception(f"{error_msg} User: {user_id}")
        audit.log("tool_failure", user_id=user_id, tool_name="github_create_pr", error=error_msg)
        return {"success": False, "error": error_msg, "suggestion": "Investigate logs for details."}

# --- Cherry-Pick Request (Implemented as Issue Creation) ---
async def github_request_cherry_pick(
    target_branch: str,
    pr_number: int,
    repo_name: Optional[str],
    user_id: str,
    user_name: str, # For issue body context
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Creates a GitHub issue to formally request cherry-picking a PR.

    Fetches details of the source PR.
    Creates a new issue in the specified (or default) repository,
    tagging relevant users/teams if possible and including details
    about the source PR and target branch.

    Args:
        target_branch: The desired target branch for the cherry-pick.
        pr_number: The source pull request number.
        repo_name: Repository name ('owner/repo') or None to use memory/default.
        user_id: ID of the user making the request.
        user_name: Name of the user making the request.
        memory: Memory interface for resolving default repository.

    Returns:
        Dict: Success dictionary with details of the created issue or error dictionary.
    """
    github: Optional[Github] = get_github_client()
    if not github: return {"error": "GitHub client not configured."}
    logger.info(f"User {user_id} ({user_name}) requesting cherry-pick of PR #{pr_number} to branch '{target_branch}' in repo {repo_name}")
    audit.log("tool_attempt", user_id=user_id, tool_name="github_request_cherry_pick", params=locals())

    try:
        repo = github.get_repo(repo_name)
        # Fetch the original pull request to get its details
        pull = repo.get_pull(pr_number)

    except UnknownObjectException as e:
        error_msg = f"Could not find repository '{repo_name}' or PR #{pr_number}."
        if "pull request" in str(e).lower():
            error_msg = f"Pull Request #{pr_number} not found in repository '{repo_name}'."
        else:
            error_msg = f"Repository '{repo_name}' not found or access denied."
        logger.error(f"{error_msg} User: {user_id}")
        audit.log("tool_failure", user_id=user_id, tool_name="github_request_cherry_pick", error=error_msg)
        return {"success": False, "error": error_msg, "suggestion": "Verify repository name, PR number, and bot permissions."}
    except GithubException as e:
        error_msg = f"GitHub API error accessing repo/PR '{repo_name}#{pr_number}': {e.status} {e.data.get('message', 'Unknown error')}"
        logger.error(f"{error_msg} User: {user_id}", exc_info=True)
        audit.log("tool_failure", user_id=user_id, tool_name="github_request_cherry_pick", error=error_msg, details=str(e.data))
        return {"success": False, "error": error_msg, "suggestion": "Check GitHub API status or bot permissions."}
    except Exception as e:
        error_msg = f"Unexpected error getting repository or PR '{repo_name}#{pr_number}': {e}"
        logger.exception(f"{error_msg} User: {user_id}")
        audit.log("tool_failure", user_id=user_id, tool_name="github_request_cherry_pick", error=error_msg)
        return {"success": False, "error": error_msg, "suggestion": "Investigate logs for details."}

    # Construct Issue Details
    issue_title = f"Request: Cherry-pick PR #{pr_number} to {target_branch}"
    issue_body = (
        f"**User Request:** User **{user_name}** ({user_id}) requested a cherry-pick via ChatOps.\n"
        f"**Pull Request:** [{pull.title}]({pull.html_url}) (#{pr_number})\n"
        f"**Source Branch:** `{pull.head.ref}`\n"
        f"**Target Branch:** `{target_branch}`\n\n"
        f"Please review PR #{pr_number} and cherry-pick the necessary commits to the `{target_branch}` branch."
        # Optionally mention the user who requested it?
        # f"\nRequested by: @{github_client.get_user(user_name).login if user_name else user_id}" # Requires lookup
    )

    try:
        # Create the GitHub issue
        # Consider adding labels (e.g., 'cherry-pick-request', 'chatops') or assignees if desired/configured
        # labels = ["cherry-pick-request", "chatops"]
        # assignees = ["some-user"]
        created_issue: Issue = repo.create_issue(
            title=issue_title, 
            body=issue_body, 
            # labels=labels,
            # assignees=assignees
        )

        success_msg = f"Successfully created issue #{created_issue.number} to track cherry-pick request for PR #{pr_number} to {target_branch}."
        logger.info(f"{success_msg} URL: {created_issue.html_url}. User: {user_id}")
        audit.log("tool_success", user_id=user_id, tool_name="github_request_cherry_pick", issue_number=created_issue.number, issue_url=created_issue.html_url, pr_number=pr_number, target_branch=target_branch)
        return {
            "success": True,
            "issue_number": created_issue.number,
            "issue_url": created_issue.html_url,
            "message": success_msg
        }

    except GithubException as e:
        error_detail = e.data.get('message', 'Unknown GitHub error')
        suggestion = "Check bot permissions to create issues."
        if e.status == 403: error_detail = "Permission denied to create issues."
        elif e.status == 410: error_detail = "Issues are disabled for this repository."

        logger.error(f"GitHub API error creating issue in {repo_name} for cherry-pick request: {e.status} {error_detail}. User: {user_id}", exc_info=False)
        audit.log("tool_failure", user_id=user_id, tool_name="github_request_cherry_pick", error=f"{e.status} {error_detail}", details=str(e.data))
        return {"success": False, "error": error_detail, "suggestion": suggestion}

    except Exception as e:
        error_msg = f"Unexpected error creating cherry-pick request issue in {repo_name}: {e}"
        logger.exception(f"{error_msg} User: {user_id}")
        audit.log("tool_failure", user_id=user_id, tool_name="github_request_cherry_pick", error=error_msg)
        return {"success": False, "error": error_msg, "suggestion": "Investigate logs for details."}

# TODO: Implement github_request_cherry_pick
# This is complex as PyGithub doesn't have a direct cherry-pick method.
# It might involve creating a branch, attempting a merge with cherry-pick strategy (not standard API),
# or more likely, creating an ISSUE or JIRA ticket requesting the cherry-pick.
# Clarify the exact requirement for "cherry-pick requests".

# Example placeholder if it creates a GitHub issue:
# async def github_request_cherry_pick(
#     repo_name: str,
#     pr_number: int,
#     target_branch: str,
#     user_id: str,
#     user_name: str
# ) -> Dict[str, Any]:
#     logger.info(f"User {user_id} requested cherry-pick of PR #{pr_number} to {target_branch} in {repo_name}")
#     github_client: Optional[Github] = get_github_client()
#     if not github_client: return {"success": False, "error": "GitHub client unavailable", "suggestion": "Check config."}
#     try:
#         repo = github_client.get_repo(repo_name)
#         pull = repo.get_pull(pr_number)
#         # Create a GitHub issue requesting the cherry-pick
#         issue_title = f"Request: Cherry-pick PR #{pr_number} ({pull.title}) to {target_branch}"
#         issue_body = f"User @{user_name} ({user_id}) requested a cherry-pick of PR #{pr_number} to the `{target_branch}` branch.\n\nPR: {pull.html_url}"
#         # Add assignee, labels as needed
#         created_issue = repo.create_issue(title=issue_title, body=issue_body)
#         audit.log("tool_success", user_id=user_id, tool_name="github_request_cherry_pick", issue_number=created_issue.number, issue_url=created_issue.html_url)
#         return {"success": True, "message": f"Created issue #{created_issue.number} to track cherry-pick request.", "issue_url": created_issue.html_url}
#     except Exception as e:
#         # ... error handling ...
#         return {"success": False, "error": ..., "suggestion": ...} 