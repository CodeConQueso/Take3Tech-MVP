import logging
import logging.handlers
import sys
import os
import traceback
import json
from datetime import datetime
import hashlib
import hmac

from fastapi import FastAPI, Request, Response, Body, HTTPException, Depends, BackgroundTasks, Header
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseCallNext
from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
from botbuilder.integration.aiohttp import CloudAdapter
from botbuilder.schema import Activity, ActivityTypes, ConversationParameters, ChannelAccount, ConversationReference

# Rate Limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Local Imports
from utils.config import settings
from bot_core import TeamsGeminiBot, CardGenerator # Import CardGenerator if needed here
from utils.scheduler import start_scheduler, stop_scheduler, reschedule_pending_digests
from memory import current_memory, RedisMemory, InMemoryMemory
from utils.api_clients import get_github_client, get_jira_client, get_gemini_client, get_octopus_client, get_perplexity_client
from utils.audit_logger import audit
from utils.security import verify_github_signature, verify_octopus_signature
from utils.helpers import create_adaptive_card_attachment # For webhook card sending
import cards

# Import the new handlers
from webhook_handlers import process_github_event, process_octopus_event

# --- Logging Setup ---
LOG_LEVEL = settings.LOG_LEVEL; log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(); logger.setLevel(LOG_LEVEL)
stream_handler = logging.StreamHandler(sys.stdout); stream_handler.setFormatter(log_formatter); logger.addHandler(stream_handler)
log_dir = "logs";
if not os.path.exists(log_dir): os.makedirs(log_dir)
log_file = os.path.join(log_dir, "chatops_bot.log")
file_handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5); file_handler.setFormatter(log_formatter); logger.addHandler(file_handler)
# Suppress verbose logs...
logging.getLogger("botbuilder").setLevel(logging.INFO if LOG_LEVEL == "DEBUG" else logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.INFO) # etc...
logger.info(f"Logging configured. Level: {LOG_LEVEL}. Outputting to console and {log_file}")
logger.info(f"Audit logging configured. Outputting to {settings.AUDIT_LOG_FILE}")
logger.info("Starting ChatOps Bot application...")

# --- Rate Limiter Setup ---
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_IP_DEFAULT])

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Teams Gemini ChatOps Bot - Enhanced",
    description="FastAPI backend for the Teams Gemini ChatOps Bot, handling message processing, webhooks, and background tasks.",
    version="1.0.0" # Example version
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# --- Bot Framework & Bot Initialization ---
ADAPTER_SETTINGS = BotFrameworkAdapterSettings(app_id=settings.MICROSOFT_APP_ID, app_password=settings.MICROSOFT_APP_PASSWORD)
ADAPTER = CloudAdapter(ADAPTER_SETTINGS)
try:
    BOT = TeamsGeminiBot(ADAPTER)
except ValueError as e:
    logger.critical(f"Failed to initialize bot: {e}. Exiting.")
    sys.exit(1)
CARD_GENERATOR = CardGenerator() # Instantiate helper

# --- Global Bot Error Handler ---
async def on_error(context: TurnContext, error: Exception):
    """Handles uncaught exceptions during Bot Framework turn processing."""
    user_id = "unknown";
    try: user_id = context.activity.from_property.id
    except Exception: pass
    logger.error(f"Unhandled exception in bot turn processing for user {user_id}: {error}", exc_info=True)
    audit.log("bot_turn_error", user_id=user_id, error=str(error), traceback=traceback.format_exc())
    await context.send_trace_activity("Bot Turn Error", label="Unhandled Error", value=f"{error}\n{traceback.format_exc()}", value_type="https://www.botframework.com/schemas/error")
    try:
        error_card = cards.create_error_card("Sorry, an internal error occurred.", "Please try again later.")
        await context.send_activity(Activity(type=ActivityTypes.message, attachments=[create_adaptive_card_attachment(error_card)]))
    except Exception as e: logger.error(f"Exception sending error message to user: {e}")
ADAPTER.on_turn_error = on_error

# --- API Endpoint for Teams Messages ---
@app.post("/api/messages", tags=["Bot Framework"])
async def messages(req: Request, background_tasks: BackgroundTasks):
    """
    Main endpoint for receiving messages/activities from Microsoft Teams via the Bot Framework.

    Processes incoming activities using the CloudAdapter and the TeamsGeminiBot.
    Handles authentication validation and dispatches processing to the bot's turn handler.
    Returns an appropriate response based on the adapter's processing.
    """
    if "application/json" not in req.headers.get("Content-Type", ""):
        return Response(status_code=415) # Unsupported Media Type

    body = await req.json()
    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    async def aux_func(turn_context: TurnContext):
        await BOT.on_turn(turn_context, background_tasks)

    try:
        response = await ADAPTER.process(req, activity, aux_func)
        if response:
            return JSONResponse(content=response.body, status_code=response.status)
        else:
            return Response(status_code=200)
    except HTTPException as http_exc:
        return JSONResponse(status_code=http_exc.status_code, content={"message": http_exc.detail})
    except Exception as e:
        user_id = activity.from_property.id if activity and activity.from_property else "unknown"
        logger.error(f"Error in /api/messages for user {user_id}: {e}", exc_info=True)
        audit.log("api_call_error", user_id=user_id, endpoint="/api/messages", status=500, error=str(e))
        return JSONResponse(status_code=500, content={"message": f"Internal server error: {e}"})

# --- Webhook Processing Task ---
# DELETE THE OLD process_webhook_event function entirely (approx lines 94-145 in the previous file view)
# async def process_webhook_event(source: str, payload: Dict | Any, target_map: Dict[str, str]):
#     ... (delete all lines of this function) ...

# --- Webhook Endpoints ---
@app.post("/api/webhooks/github", tags=["Webhooks"])
@limiter.limit("200/minute") # Allow higher rate
async def handle_github_webhook(req: Request, background_tasks: BackgroundTasks, payload: dict = Body(...), x_hub_signature_256: str | None = Header(None), x_github_event: str | None = Header(None), x_github_delivery: str | None = Header(None)):
    """
    Receives webhook events from GitHub.

    Verifies the signature (if GITHUB_WEBHOOK_SECRET is configured).
    Extracts relevant information (repo name, event type).
    Dispatches the event processing to the `process_github_event` handler via background tasks.
    """
    source = "github"; webhook_id = x_github_delivery or "unknown"; ip_address = get_remote_address(req)
    audit.log("webhook_received", user_id="webhook", source=source, webhook_id=webhook_id, ip=ip_address, event_type=x_github_event)
    raw_body = await req.body()

    if settings.GITHUB_WEBHOOK_SECRET and not verify_github_signature(raw_body, settings.GITHUB_WEBHOOK_SECRET, x_hub_signature_256):
         audit.log("webhook_security_fail", user_id="webhook", source=source, ip=ip_address, reason="Invalid signature"); raise HTTPException(status_code=403, detail="Invalid GitHub signature")

    # Extract necessary info for the new handler
    repo_name = payload.get("repository", {}).get("full_name")
    event_type = x_github_event or payload.get("action") # Use header first, fallback to payload action if needed

    if not repo_name:
        logger.error("Could not determine repository name from GitHub webhook payload.")
        audit.log("webhook_processing_error", user_id="webhook", source=source, webhook_id=webhook_id, error="Missing repository name")
        # Decide if you want to return 400 or just 202
        return Response(status_code=400, content="Missing repository name in payload")

    if not event_type:
        logger.error("Could not determine event type from GitHub webhook headers or payload.")
        audit.log("webhook_processing_error", user_id="webhook", source=source, webhook_id=webhook_id, error="Missing event type")
        return Response(status_code=400, content="Missing event type")


    # Call the new handler via background task
    background_tasks.add_task(
        process_github_event,
        adapter=ADAPTER,
        background_tasks=background_tasks, # Pass it along if the handler needs to schedule more tasks
        current_memory=current_memory,
        settings=settings,
        event_type=event_type,
        payload=payload,
        repo_name=repo_name
    )
    return Response(status_code=202) # Acknowledge processing

@app.post("/api/webhooks/octopus", tags=["Webhooks"])
@limiter.limit("200/minute")
async def handle_octopus_webhook(req: Request, background_tasks: BackgroundTasks, payload: dict = Body(...), x_octopus_signature: str | None = Header(None)):
    """
    Receives webhook events from Octopus Deploy.

    Verifies the signature (if OCTOPUS_WEBHOOK_SECRET is configured).
    Extracts relevant information (project ID, event category).
    Dispatches the event processing to the `process_octopus_event` handler via background tasks.
    """
    source = "octopus"; webhook_id = payload.get("Subscription", {}).get("Id", "unknown"); ip_address = get_remote_address(req)
    event_category = payload.get("Payload", {}).get("EventCategory", "Unknown") # Extract event category
    audit.log("webhook_received", user_id="webhook", source=source, webhook_id=webhook_id, ip=ip_address, event_type=event_category)
    raw_body = await req.body()

    # Use the existing verify_octopus_signature placeholder/implementation
    if not verify_octopus_signature(raw_body, settings.OCTOPUS_WEBHOOK_SECRET, x_octopus_signature): # Checks if secret is set
         audit.log("webhook_security_fail", user_id="webhook", source=source, ip=ip_address, reason="Invalid signature"); raise HTTPException(status_code=403, detail="Invalid Octopus signature")

    # Extract necessary info for the new handler
    # Assuming project ID is nested within the payload structure, adjust if necessary
    project_id = payload.get("Payload", {}).get("Event", {}).get("ProjectId")
    # Example: Extracting based on common Octopus webhook structure for deployments
    # project_id = payload.get("Payload", {}).get("Event", {}).get("RelatedDocumentIds", [None])[0] # If project ID is in RelatedDocumentIds
    # project_id = payload.get("Event", {}).get("ProjectId") # Simpler structure? Need to confirm payload format.

    if not project_id:
        logger.error("Could not determine project ID from Octopus webhook payload.")
        audit.log("webhook_processing_error", user_id="webhook", source=source, webhook_id=webhook_id, error="Missing project ID")
        return Response(status_code=400, content="Missing project ID in payload")

    # Call the new handler via background task
    background_tasks.add_task(
        process_octopus_event,
        adapter=ADAPTER,
        background_tasks=background_tasks, # Pass it along
        current_memory=current_memory,
        settings=settings,
        event_category=event_category,
        payload=payload, # Pass the whole payload
        project_id=project_id
    )
    return Response(status_code=202) # Acknowledge processing

# --- Health Check Endpoint ---
@app.get("/health", tags=["Monitoring"]) # Add a tag for API docs
async def health_check():
    """Performs basic health checks for the bot service, including memory store, LLM client, and scheduler status."""
    health_status = {"status": "ok", "checks": {}}
    overall_status = 200

    # Check Memory Store
    memory_backend_type = type(current_memory).__name__
    health_status["checks"]["memory_store"] = {"type": memory_backend_type, "status": "ok"}
    try:
        if isinstance(current_memory, RedisMemory):
            # Use the built-in check with retry
            await current_memory._check_connection() 
            health_status["checks"]["memory_store"]["message"] = "Redis connection successful."
        elif isinstance(current_memory, InMemoryMemory):
            # Simple check for in-memory
            await current_memory.get_user_data("health_check_test") # Perform a dummy operation
            health_status["checks"]["memory_store"]["message"] = "In-memory store accessible."
        else:
             health_status["checks"]["memory_store"]["status"] = "unknown"
             health_status["checks"]["memory_store"]["message"] = "Memory backend type not recognized for specific check."
    except Exception as e:
        logger.error(f"Health Check: Memory store check failed - {e}", exc_info=False)
        health_status["checks"]["memory_store"]["status"] = "error"
        health_status["checks"]["memory_store"]["message"] = str(e)
        health_status["status"] = "error"
        overall_status = 503 # Service Unavailable

    # Check Gemini Client
    gemini_client = get_gemini_client()
    health_status["checks"]["llm_service"] = {"provider": "Gemini", "status": "ok"}
    if not gemini_client:
        health_status["checks"]["llm_service"]["status"] = "error"
        health_status["checks"]["llm_service"]["message"] = "Gemini client not initialized or configured."
        health_status["status"] = "error"
        overall_status = 503
    # Optional: Add a lightweight test call like list_models if available and cheap
    # try:
    #    await gemini_client.list_models_async()
    # except Exception as e:
    #    health_status["checks"]["llm_service"]["status"] = "error"
    #    health_status["checks"]["llm_service"]["message"] = f"Gemini API connection test failed: {e}"
    #    health_status["status"] = "error"
    #    overall_status = 503
        
    # Check Scheduler Status (Basic)
    health_status["checks"]["scheduler"] = {"status": "ok"}
    try:
        if scheduler.running:
            health_status["checks"]["scheduler"]["state"] = "running"
            # Could add job count: len(scheduler.get_jobs())
        else:
             health_status["checks"]["scheduler"]["status"] = "error"
             health_status["checks"]["scheduler"]["state"] = "stopped"
             health_status["status"] = "error"
             overall_status = 503
    except Exception as e:
        logger.error(f"Health Check: Error checking scheduler status - {e}", exc_info=False)
        health_status["checks"]["scheduler"]["status"] = "error"
        health_status["checks"]["scheduler"]["message"] = str(e)
        health_status["status"] = "error"
        overall_status = 503

    # Add more checks as needed (e.g., other API client basic pings)

    return JSONResponse(content=health_status, status_code=overall_status)

# --- Application Startup/Shutdown ---
@app.on_event("startup")
async def startup_event():
    """
    Performs initialization tasks when the FastAPI application starts.

    - Starts the background scheduler (for digests).
    - Initializes API clients.
    - Checks the memory store connection (if Redis).
    - Reschedules any pending daily digests.
    """
    logger.info("FastAPI application starting up..."); start_scheduler()
    logger.info("Checking API client status..."); # Call getters...
    get_github_client(); get_jira_client(); get_gemini_client(); get_octopus_client(); get_perplexity_client()
    if not get_gemini_client(): logger.critical("Gemini client failed startup check.")
    if isinstance(current_memory, RedisMemory): # Check Redis connection specifically
        try: await current_memory._check_connection(); logger.info("Redis connection checked on startup.")
        except Exception as e: logger.error(f"Initial Redis check failed: {e}")
    await reschedule_pending_digests() # Attempt to reschedule digests
    logger.info("Startup complete.")

@app.on_event("shutdown")
async def shutdown_event():
    """
    Performs cleanup tasks when the FastAPI application shuts down.

    - Stops the background scheduler.
    - Closes asynchronous HTTP clients (Octopus, Perplexity).
    - Closes the memory store connection pool (if Redis).
    """
    logger.info("FastAPI application shutting down..."); stop_scheduler()
    # Close HTTPX clients
    for client in [get_octopus_client(), get_perplexity_client()]:
        if client and hasattr(client, "aclose"):
            try: await client.aclose(); logger.info(f"Closed client: {client.base_url}")
            except Exception as e: logger.warning(f"Error closing client {client.base_url}: {e}")
    # Close Redis pool
    if isinstance(current_memory, RedisMemory) and hasattr(current_memory, 'close'): await current_memory.close()
    logger.info("Shutdown complete.")

# --- Run Locally ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting Uvicorn server locally on http://0.0.0.0:{port}...")
    # Use reload=False if background tasks or scheduler have issues with it
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True) 