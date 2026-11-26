"""Symmetric checkpoint authentication fixture; key and latest tag are external."""

import hashlib
import hmac
import json


def _signed_bytes(company, checkpoint):
    if not isinstance(company, str) or not company:
        raise ValueError("CHECKPOINT_INVALID")
    if not isinstance(checkpoint, dict) or set(checkpoint) != {"receipt", "authorization", "history"}:
        raise ValueError("CHECKPOINT_INVALID")
    return json.dumps(
        {"company": company, "checkpoint": checkpoint},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")


def sign_checkpoint(company, checkpoint, key):
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("CHECKPOINT_KEY_INVALID")
    signature = hmac.new(
        key, b"BN-V3-RECOVERY-PROOF\0" + _signed_bytes(company, checkpoint), hashlib.sha256
    ).hexdigest()
    return {"company": company, "checkpoint": checkpoint, "signature": signature}


def verify_checkpoint(envelope, company, key, expected_signature):
    if not isinstance(envelope, dict) or set(envelope) != {"company", "checkpoint", "signature"}:
        raise ValueError("CHECKPOINT_INVALID")
    if envelope["company"] != company:
        raise ValueError("CHECKPOINT_DENIED")
    signature = envelope["signature"]
    for tag in (signature, expected_signature):
        if not isinstance(tag, str) or len(tag) != 64 or any(c not in "0123456789abcdef" for c in tag):
            raise ValueError("CHECKPOINT_DENIED")
    calculated = sign_checkpoint(company, envelope["checkpoint"], key)["signature"]
    if not hmac.compare_digest(signature, calculated):
        raise ValueError("CHECKPOINT_DENIED")
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("CHECKPOINT_STALE")
    return envelope["checkpoint"]
