import logging
from typing import Dict, Any, Optional
import httpx

from .schemas import PerplexitySearchParams
from memory.interface import MemoryInterface
from utils.api_clients import get_perplexity_client, retry_transient # Import retry decorator
from utils.audit_logger import audit # Added
from utils.api_clients import PerplexityClient

logger = logging.getLogger(__name__)

@retry_transient
async def _search_perplexity_internal(client: httpx.AsyncClient, payload: dict) -> dict:
     """Internal helper to make Perplexity API call with retry."""
     # Base URL is handled by the client instance
     response = await client.post("/chat/completions", json=payload)
     response.raise_for_status()
     return response.json()


async def search_perplexity(
    query: str,
    user_id: str,
    # memory: MemoryInterface # Not currently used
    **kwargs
) -> Dict[str, Any]:
    """
    Performs a search using the Perplexity AI API.

    Args:
        query: The search query string.
        user_id: ID of the user making the request.

    Returns:
        Dict: Success dictionary with search results or error dictionary.
    """
    pplx: Optional[PerplexityClient] = get_perplexity_client()
    if not pplx: return {"error": "Perplexity client not configured."}

    logger.info(f"Executing tool: search_perplexity for query: '{query[:50]}...'. User: {user_id}")
    audit.log("tool_attempt", user_id=user_id, tool_name="perplexity_search", params=kwargs)

    payload = {
        "model": "sonar-small-online", # Use a model capable of online search
        "messages": [
            {"role": "system", "content": "You are an AI assistant. Provide a concise and informative answer to the user's query based on web search results."},
            {"role": "user", "content": query},
        ],
        "max_tokens": 512, # Adjust as needed
        "temperature": 0.3, # Lower temperature for factual search
    }

    try:
        result_data = await _search_perplexity_internal(pplx, payload) # Use internal func with retry

        if result_data.get("choices") and len(result_data["choices"]) > 0:
            choice = result_data["choices"][0]
            answer = choice.get("message", {}).get("content")
            finish_reason = choice.get("finish_reason", "unknown")
            usage = result_data.get("usage", {})
            
            logger.info(f"Perplexity search successful (finish: {finish_reason}). Usage: {usage.get('total_tokens', '?')} tokens. User: {user_id}")
            
            if answer:
                 await memory.set_user_data(user_id, "last_perplexity_query", query)
                 audit.log("tool_success", user_id=user_id, tool_name="perplexity_search", query=query, finish_reason=finish_reason, usage=usage)
                 return {"success": True, "query": query, "answer": answer}
            else:
                 # Handle cases where content might be missing despite success
                 error_msg = f"Perplexity returned a successful response but no answer content (finish reason: {finish_reason})."
                 logger.warning(f"{error_msg} User: {user_id}")
                 audit.log("tool_failure", user_id=user_id, tool_name="perplexity_search", query=query, error=error_msg, finish_reason=finish_reason)
                 return {"success": False, "error": error_msg, "suggestion": "Try rephrasing the query or check Perplexity status."}
        else:
            # Handle unexpected response structure
            error_msg = f"Perplexity returned an unexpected response format: {result_data}"
            logger.error(f"{error_msg} User: {user_id}")
            audit.log("tool_failure", user_id=user_id, tool_name="perplexity_search", query=query, error="Unexpected response format", details=str(result_data))
            return {"success": False, "error": "Perplexity returned an unexpected response format.", "suggestion": "Contact support or check Perplexity API status."}

    except httpx.HTTPStatusError as e:
        error_text = "Unknown error"
        status_code = e.response.status_code
        try:
            error_data = e.response.json()
            error_text = error_data.get("error", {}).get("message", e.response.text)
        except Exception:
            error_text = e.response.text or f"Status {status_code}"
        
        logger.error(f"Perplexity API error ({status_code}): {error_text}. User: {user_id}", exc_info=False)
        suggestion = "Check API key and query."
        if status_code in [401, 403]: 
            suggestion = "Authentication error. Check PERPLEXITY_API_KEY."
        elif status_code == 429: 
            suggestion = "Rate limit exceeded. Wait and try again."
        elif status_code == 400:
            suggestion = f"Invalid request. Check query or parameters. Details: {error_text}"
            
        audit.log("tool_failure", user_id=user_id, tool_name="perplexity_search", query=query, error=f"API Error ({status_code}): {error_text}")
        return {"success": False, "error": f"Perplexity API Error ({status_code}).", "suggestion": suggestion}
    except Exception as e:
        logger.exception(f"Unexpected error during Perplexity search: {e}. User: {user_id}")
        audit.log("tool_failure", user_id=user_id, tool_name="perplexity_search", query=query, error=f"Unexpected error: {e}")
        return {"success": False, "error": "An unexpected error occurred during Perplexity search.", "suggestion": "Investigate bot logs."} 