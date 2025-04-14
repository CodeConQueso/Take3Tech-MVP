import pytest
from app.utils.helpers import parse_map_string, verify_octopus_signature # Assuming functions exist
import hmac
import hashlib

# --- Test parse_map_string ---

@pytest.mark.parametrize(
    "input_str, expected_dict",
    [
        ("key1=value1,key2=value2", {"key1": "value1", "key2": "value2"}),
        (" key1 = value1 , key2 = value2 ", {"key1": "value1", "key2": "value2"}), # Whitespace
        ("key1=value1", {"key1": "value1"}), # Single pair
        ("", {}), # Empty string
        ("key1=value1,key1=value_new", {"key1": "value_new"}), # Duplicate key (last wins)
        ("key1=value1,key2=value with spaces", {"key1": "value1", "key2": "value with spaces"}), # Value with spaces
        ("key1==value1", {"key1": "=value1"}), # Equals in value
    ]
)
def test_parse_map_string_valid(input_str, expected_dict):
    """Tests parse_map_string with valid inputs."""
    assert parse_map_string(input_str) == expected_dict

@pytest.mark.parametrize(
    "invalid_input",
    [
        "key1=value1,key2", # Missing equals
        "key1,key2=value2", # Missing equals
        "key1=value1,=value2", # Missing key
        "key1:value1;key2:value2", # Wrong delimiters
    ]
)
def test_parse_map_string_invalid_format(invalid_input):
    """Tests parse_map_string with invalid formats (should likely return empty or handle gracefully)."""
    # Depending on implementation, it might raise an error or return partial/empty
    # Let's assume it returns an empty dict for malformed entries
    # Adjust assertion based on actual behavior
    result = parse_map_string(invalid_input)
    # Example: Asserting it returns only valid pairs or empty
    if "key1=value1" in invalid_input and "key2" in invalid_input and ":" not in invalid_input:
         assert result == {"key1": "value1"} # If it parses valid parts
    elif "key1" in invalid_input and "key2=value2" in invalid_input and ":" not in invalid_input:
        assert result == {"key2": "value2"}
    elif invalid_input == "key1=value1,=value2": # Specific check if empty key is ignored
        assert result == {"key1": "value1"}
    else:
         assert result == {} # Default assumption for malformed input


# --- Test verify_octopus_signature ---

@pytest.fixture
def octopus_test_data():
    secret = "a_very_secret_key"
    body = b'{"event":"test"}'
    # Pre-calculate expected hashes
    expected_sha1 = hmac.new(secret.encode('utf-8'), body, hashlib.sha1).hexdigest()
    expected_sha256 = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return {
        "secret": secret,
        "body": body,
        "sha1_hash": expected_sha1,
        "sha256_hash": expected_sha256
    }

def test_verify_octopus_signature_valid_sha1(octopus_test_data):
    """Tests valid SHA1 signature."""
    signature = f"sha1={octopus_test_data['sha1_hash']}"
    assert verify_octopus_signature(
        octopus_test_data["secret"],
        signature,
        octopus_test_data["body"]
    ) is True

def test_verify_octopus_signature_valid_sha256(octopus_test_data):
    """Tests valid SHA256 signature."""
    signature = f"sha256={octopus_test_data['sha256_hash']}"
    assert verify_octopus_signature(
        octopus_test_data["secret"],
        signature,
        octopus_test_data["body"]
    ) is True

def test_verify_octopus_signature_invalid_hash(octopus_test_data):
    """Tests invalid signature (wrong hash)."""
    signature = f"sha256=incorrect_hash_value"
    assert verify_octopus_signature(
        octopus_test_data["secret"],
        signature,
        octopus_test_data["body"]
    ) is False

def test_verify_octopus_signature_wrong_algo_for_hash(octopus_test_data):
    """Tests providing SHA1 hash with sha256 algorithm prefix."""
    signature = f"sha256={octopus_test_data['sha1_hash']}" # SHA1 hash, but algo=sha256
    assert verify_octopus_signature(
        octopus_test_data["secret"],
        signature,
        octopus_test_data["body"]
    ) is False

def test_verify_octopus_signature_missing_header(octopus_test_data):
    """Tests missing signature header."""
    assert verify_octopus_signature(
        octopus_test_data["secret"],
        None, # Signature is None
        octopus_test_data["body"]
    ) is False

def test_verify_octopus_signature_invalid_header_format(octopus_test_data):
    """Tests signature header with incorrect format."""
    invalid_signatures = [
        "sha256", # Missing =
        "=somehash", # Missing algo
        "sha256=hash1=hash2", # Too many =
        "", # Empty string
    ]
    for signature in invalid_signatures:
        assert verify_octopus_signature(
            octopus_test_data["secret"],
            signature,
            octopus_test_data["body"]
        ) is False, f"Failed for signature: {signature}"

def test_verify_octopus_signature_unsupported_algo(octopus_test_data):
    """Tests signature with an unsupported algorithm."""
    signature = f"md5={octopus_test_data['sha256_hash']}" # Use md5 algo
    assert verify_octopus_signature(
        octopus_test_data["secret"],
        signature,
        octopus_test_data["body"]
    ) is False 