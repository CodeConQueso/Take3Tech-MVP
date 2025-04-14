import logging
from typing import Dict, Any, Optional, Tuple
import httpx

from .schemas import OctopusDeployParams
from memory.interface import MemoryInterface
from utils.api_clients import get_octopus_client, retry_transient # Import retry decorator
from utils.config import settings
from utils.audit_logger import audit # Added

logger = logging.getLogger(__name__)

@retry_transient
async def _make_octopus_request(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    """Internal helper for making Octopus requests with retry."""
    response = await client.request(method, path, **kwargs)
    response.raise_for_status() # Raise HTTPStatusError for bad responses (4xx, 5xx) after retries
    return response

async def find_octopus_resource_id(client: httpx.AsyncClient, space_id: str, resource_type: str, resource_name: str) -> Optional[str]:
    """Finds the ID of an Octopus resource by name within a space. Retries transients."""
    search_path = f"{space_id}/{resource_type}?partialName={resource_name}&take=1" # Relative path from /api
    logger.debug(f"Searching Octopus for {resource_type} '{resource_name}' in space {space_id} via path: api/{search_path}")
    try:
        response = await _make_octopus_request(client, "GET", search_path)
        data = response.json()
        items = data.get("Items", [])
        # Case-insensitive comparison
        for item in items:
             if item.get("Name", "").lower() == resource_name.lower():
                 resource_id = item.get("Id")
                 logger.debug(f"Found exact match for {resource_type} '{resource_name}': ID {resource_id}")
                 return resource_id
        logger.warning(f"Could not find exact match for {resource_type} '{resource_name}' in space {space_id}. Check names and permissions.")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error finding Octopus {resource_type} '{resource_name}': {e.response.status_code} - {e.response.text}")
        return None # Return None on error
    except Exception as e:
        logger.error(f"Error finding Octopus {resource_type} '{resource_name}': {e}", exc_info=True)
        return None

async def find_space_id(client: httpx.AsyncClient, space_name: str) -> Optional[str]:
     """Finds the ID of an Octopus Space by name. Retries transients."""
     search_path = f"spaces?partialName={space_name}&take=1" # Global path
     logger.debug(f"Searching Octopus for Space '{space_name}' via path: api/{search_path}")
     try:
        response = await _make_octopus_request(client, "GET", search_path)
        data = response.json()
        items = data.get("Items", [])
        for item in items:
             if item.get("Name", "").lower() == space_name.lower():
                 space_id = item.get("Id")
                 logger.debug(f"Found exact match for Space '{space_name}': ID {space_id}")
                 return space_id
        logger.warning(f"Could not find exact match for Space '{space_name}'. Check name and permissions.")
        return None
     except httpx.HTTPStatusError as e:
         logger.error(f"HTTP error finding Octopus Space '{space_name}': {e.response.status_code} - {e.response.text}")
         return None
     except Exception as e:
         logger.error(f"Error finding Octopus Space '{space_name}': {e}", exc_info=True)
         return None

async def get_latest_release_id(client: httpx.AsyncClient, space_id: str, project_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Gets the ID and version of the latest release for a project. Retries transients."""
    releases_path = f"{space_id}/projects/{project_id}/releases?orderBy=Version&order=Descending&take=1"
    logger.debug(f"Getting latest release for project {project_id} in space {space_id} via path: api/{releases_path}")
    try:
        response = await _make_octopus_request(client, "GET", releases_path)
        data = response.json()
        items = data.get("Items", [])
        if items:
             latest_release = items[0]
             release_id = latest_release.get("Id")
             release_version = latest_release.get("Version")
             logger.debug(f"Found latest release: ID {release_id}, Version {release_version}")
             return release_id, release_version
        else:
             logger.warning(f"No releases found for project {project_id} in space {space_id}")
             return None, None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error getting latest release for project {project_id}: {e.response.status_code} - {e.response.text}")
        return None, None
    except Exception as e:
        logger.error(f"Error getting latest release for project {project_id}: {e}", exc_info=True)
        return None, None

async def get_release_id_by_version(client: httpx.AsyncClient, space_id: str, project_id: str, version: str) -> Optional[str]:
    """Gets the ID of a specific release version. Retries transients."""
    release_path = f"{space_id}/projects/{project_id}/releases?version={version}&take=1"
    logger.debug(f"Getting release ID for version '{version}' project {project_id} via path: api/{release_path}")
    try:
        response = await _make_octopus_request(client, "GET", release_path)
        data = response.json()
        items = data.get("Items", [])
        if items: 
            release_id = items[0].get("Id")
            logger.debug(f"Found release ID {release_id} for version '{version}'")
            return release_id
        else: 
            logger.warning(f"Release version '{version}' not found for project {project_id}")
            return None
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error getting release {version} for project {project_id}: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Error getting release {version} for project {project_id}: {e}", exc_info=True)
        return None


async def trigger_octopus_deployment(
    project_name: Optional[str],
    environment_name: Optional[str],
    space_name: Optional[str],
    release_version: Optional[str],
    tenant_name: Optional[str],
    comments: Optional[str],
    user_id: str,
    memory: MemoryInterface,
    **kwargs
) -> Dict[str, Any]:
    """
    Triggers a deployment in Octopus Deploy.

    Resolves project, environment, and space names using inputs, memory, or defaults.
    Finds the appropriate release (latest or specified version).
    Initiates the deployment via the Octopus Deploy API.

    Args:
        project_name: Octopus project name, or None to use memory/default.
        environment_name: Octopus environment name, or None to use memory/default.
        space_name: Octopus space name, or None to use memory/default.
        release_version: Specific release version to deploy, or None for latest.
        tenant_name: Optional tenant name for tenant-based deployments.
        comments: Optional deployment comments.
        user_id: ID of the user triggering the deployment.
        memory: Memory interface for resolving defaults.

    Returns:
        Dict: Success dictionary with deployment details or error dictionary.
    """
    octopus: Optional[httpx.AsyncClient] = get_octopus_client()
    if not octopus:
         return {"success": False, "error": "Octopus Deploy client not configured.", "suggestion": "Check OCTOPUS_SERVER and OCTOPUS_API_KEY settings."}

    # Resolve parameters using provided values, memory, or defaults
    space_name = space_name or await memory.get_user_value(user_id, "last_octopus_space") or settings.DEFAULT_OCTOPUS_SPACE_NAME
    project_name = project_name or await memory.get_user_value(user_id, "last_octopus_project") or settings.DEFAULT_OCTOPUS_PROJECT_NAME
    environment_name = environment_name or await memory.get_user_value(user_id, "last_octopus_environment") or settings.DEFAULT_OCTOPUS_ENVIRONMENT_NAME
    release_version_param = release_version
    tenant_name = tenant_name # Optional
    comments = comments or f"Deployment triggered by ChatOps bot for user {user_id}"

    # Log the attempt with resolved parameters
    audit_params = {
        "space_name": space_name, "project_name": project_name, "environment_name": environment_name,
        "release_version": release_version_param or "Latest", "tenant_name": tenant_name, "comments": comments
    }
    logger.info(f"Attempting Octopus deployment: {audit_params}")
    audit.log("tool_attempt", user_id=user_id, tool_name="octopus_trigger_deployment", params=audit_params)

    # Basic parameter validation
    if not all([space_name, project_name, environment_name]):
         missing = [k for k, v in {"space_name":space_name, "project_name":project_name, "environment_name":environment_name}.items() if not v]
         error_msg = f"Missing required Octopus parameters: {', '.join(missing)}"
         audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=error_msg)
         return {"success": False, "error": error_msg, "suggestion": "Provide space, project, environment names or set defaults."}

    try:
        # --- Find Resource IDs --- 
        space_id = await find_space_id(octopus, space_name)
        if not space_id: 
             error_msg = f"Octopus Space '{space_name}' not found or access denied."
             audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=error_msg)
             return {"success": False, "error": error_msg, "suggestion": "Verify space name and API key permissions."}
        await memory.set_user_data(user_id, "last_octopus_space", space_name) # Save context

        project_id = await find_octopus_resource_id(octopus, space_id, "projects", project_name)
        if not project_id: 
            error_msg = f"Octopus Project '{project_name}' not found in space '{space_name}'."
            audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=error_msg)
            return {"success": False, "error": error_msg, "suggestion": "Verify project name and space."}
        await memory.set_user_data(user_id, "last_octopus_project", project_name) # Save context

        environment_id = await find_octopus_resource_id(octopus, space_id, "environments", environment_name)
        if not environment_id: 
            error_msg = f"Octopus Environment '{environment_name}' not found in space '{space_name}'."
            audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=error_msg)
            return {"success": False, "error": error_msg, "suggestion": "Verify environment name and space."}
        await memory.set_user_data(user_id, "last_octopus_environment", environment_name) # Save context

        tenant_id = None
        if tenant_name:
            tenant_id = await find_octopus_resource_id(octopus, space_id, "tenants", tenant_name)
            if not tenant_id: 
                 error_msg = f"Octopus Tenant '{tenant_name}' not found in space '{space_name}'."
                 audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=error_msg)
                 return {"success": False, "error": error_msg, "suggestion": "Verify tenant name."}
            await memory.set_user_data(user_id, "last_octopus_tenant", tenant_name) # Save context

        # --- Determine Release ID --- 
        release_id = None
        actual_release_version = "(Could not determine)"
        if release_version_param:
            release_id = await get_release_id_by_version(octopus, space_id, project_id, release_version_param)
            if not release_id: 
                error_msg = f"Release version '{release_version_param}' not found for project '{project_name}' in space '{space_name}'."
                audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=error_msg)
                return {"success": False, "error": error_msg, "suggestion": "Verify release version exists."}
            actual_release_version = release_version_param
        else:
            release_id, actual_release_version_found = await get_latest_release_id(octopus, space_id, project_id)
            if not release_id: 
                error_msg = f"Could not determine latest release for project '{project_name}' in space '{space_name}'."
                audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=error_msg)
                return {"success": False, "error": error_msg, "suggestion": "Ensure project has releases."}
            actual_release_version = actual_release_version_found or "(Unknown)"
            logger.info(f"Using latest release: {actual_release_version} (ID: {release_id})")

        # --- Trigger Deployment --- 
        deployment_payload = {
            "ReleaseId": release_id, "EnvironmentId": environment_id,
            "Comments": comments, "TenantId": tenant_id, # Will be None if tenant_name wasn't provided
            "FormValues": {}, # Optional: Add form values if deployment requires them
            "ForcePackageDownload": False, "UseGuidedFailure": False,
            "SpecificMachineIds": [], "ExcludeMachineIds": [], "SkipActions": []
        }
        deploy_path = f"{space_id}/deployments"
        logger.debug(f"Sending deployment request to api/{deploy_path} with payload: {deployment_payload}")
        response = await _make_octopus_request(octopus, "POST", deploy_path, json=deployment_payload) # Use internal func with retry
        deployment_data = response.json()

        deployment_id = deployment_data.get("Id")
        task_id = deployment_data.get("TaskId")
        # Construct link based on known base URL
        server_task_link = f"{str(octopus.base_url).rstrip('/api/')}/app#/{space_id}/tasks/{task_id}" if task_id and space_id else None

        success_msg = f"Deployment of {project_name} v{actual_release_version} to {environment_name}{f' for tenant {tenant_name}' if tenant_name else ''} initiated."
        logger.info(f"{success_msg} Deployment ID: {deployment_id}, Task ID: {task_id}. User: {user_id}")
        audit.log("tool_success", user_id=user_id, tool_name="octopus_trigger_deployment", deployment_id=deployment_id, task_id=task_id, task_url=server_task_link, **audit_params)
        return {
            "success": True, 
            "deployment_id": deployment_id, 
            "task_id": task_id, 
            "server_task_link": server_task_link,
            "space_name": space_name, 
            "project_name": project_name, 
            "environment_name": environment_name,
            "release_version": actual_release_version, 
            "tenant_name": tenant_name, 
            "comments": comments,
            "message": success_msg
        }

    except httpx.HTTPStatusError as e:
        error_text = "Unknown error"
        try:
            error_data = e.response.json()
            error_text = error_data.get("ErrorMessage", e.response.text)
        except Exception:
            error_text = e.response.text or f"Status {e.response.status_code}"
        
        logger.error(f"Octopus API error during deployment trigger: {e.response.status_code} - {error_text}", exc_info=False) 
        suggestion = "Check Octopus logs, API key permissions, and configuration (space, project, env names)."
        if e.response.status_code == 400: 
            suggestion = f"Invalid request. Check parameters/IDs. Details: {error_text}"
        elif e.response.status_code in [401, 403]: 
            suggestion = "Authentication/permission error. Verify the OCTOPUS_API_KEY and its permissions."
        
        audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=f"API Error ({e.response.status_code}): {error_text}", **audit_params)
        return {"success": False, "error": f"Octopus API Error ({e.response.status_code}).", "suggestion": suggestion}
    except Exception as e:
        logger.exception(f"Unexpected error triggering Octopus deployment: {e}")
        audit.log("tool_failure", user_id=user_id, tool_name="octopus_trigger_deployment", error=f"Unexpected error: {e}", **audit_params)
        return {"success": False, "error": "An unexpected error occurred during Octopus deployment.", "suggestion": "Investigate bot logs."} 