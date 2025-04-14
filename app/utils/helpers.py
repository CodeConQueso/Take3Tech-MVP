import re
import hashlib
import hmac
from typing import Dict

from botbuilder.core import TurnContext
from botbuilder.schema import Activity

# ... other helper functions ...

def parse_map_string(map_string: str) -> Dict[str, str]:
    """Parses a string like 'key1=value1,key2=value with space' into a dictionary."""
    result = {}
    if not map_string:
        return result
    pairs = map_string.split(',')
    for pair in pairs:
        parts = pair.split('=', 1) # Split only on the first equals
        if len(parts) == 2:
            key = parts[0].strip()
            value = parts[1].strip()
            if key: # Ignore if key is empty
                result[key] = value
    return result

def verify_octopus_signature(secret: str, signature_header: str | None, request_body: bytes) -> bool:
    """Verifies the signature of an Octopus Deploy webhook request.

    Args:
        secret: The webhook shared secret.
        signature_header: The value of the X-Octopus-Signature header.
        request_body: The raw request body bytes.

    Returns:
        True if the signature is valid, False otherwise.
    """
    if not signature_header:
        # Missing signature header
        return False

    try:
        algo_str, provided_hash = signature_header.split("=")
    except ValueError:
        # Invalid header format
        return False

    secret_bytes = secret.encode('utf-8')

    if algo_str == "sha1":
        hasher = hmac.new(secret_bytes, request_body, hashlib.sha1)
    elif algo_str == "sha256":
        hasher = hmac.new(secret_bytes, request_body, hashlib.sha256)
    else:
        # Unsupported algorithm
        return False

    expected_hash = hasher.hexdigest()

    return hmac.compare_digest(expected_hash, provided_hash)

# --- Add other helper functions as needed --- 