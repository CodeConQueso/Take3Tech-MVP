import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)

def verify_github_signature(payload_body: bytes, secret_token: str | None, signature_header: str | None) -> bool:
    """
    Verifies the GitHub webhook signature (X-Hub-Signature-256).
    Requires the raw request body as bytes.
    """
    if not secret_token:
         logger.warning("GITHUB_WEBHOOK_SECRET is not set. Cannot verify GitHub signature. Allowing webhook through.")
         # Depending on policy, you might want to return False here if secret is expected.
         return True # Allow if no secret configured in bot

    if not signature_header:
        logger.warning("Missing GitHub signature header (X-Hub-Signature-256). Denying webhook.")
        return False

    if not signature_header.startswith("sha256="):
         logger.warning(f"Invalid GitHub signature format: {signature_header}")
         return False

    hash_object = hmac.new(secret_token.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()

    if not hmac.compare_digest(expected_signature, signature_header):
        logger.warning(f"Invalid GitHub signature. Expected: {expected_signature}, Got: {signature_header}")
        return False

    logger.debug("GitHub webhook signature verified successfully.")
    return True

def verify_octopus_signature(payload_body: bytes, secret_token: str | None, signature_header: str | None) -> bool:
    """
    Verifies an Octopus webhook signature (assuming HMAC-SHA256).
    Adapt based on your Octopus subscription configuration.
    """
    if not secret_token:
         logger.debug("OCTOPUS_WEBHOOK_SECRET not set, skipping signature verification.")
         return True # Allow if no secret is configured in the bot

    # Octopus header might be 'X-Octopus-Signature' or similar - check Octopus docs/config
    # Assuming it provides just the hex digest (without prefix)
    if not signature_header:
        logger.warning("Missing Octopus signature header. Denying webhook.")
        return False

    try:
        hash_object = hmac.new(secret_token.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha256)
        expected_signature = hash_object.hexdigest()

        if not hmac.compare_digest(expected_signature, signature_header):
            logger.warning("Invalid Octopus signature.")
            return False
    except Exception as e:
         logger.error(f"Error during Octopus signature verification: {e}")
         return False

    logger.debug("Octopus webhook signature verified successfully.")
    return True

# Add verification functions for other webhook sources if needed 