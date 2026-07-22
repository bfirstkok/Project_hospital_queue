import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.core.cache import cache


audit_logger = logging.getLogger("security.audit")


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    value = forwarded.split(",", 1)[0] if forwarded else request.META.get("REMOTE_ADDR", "unknown")
    return value.strip() or "unknown"


def client_fingerprint(request):
    """Return a stable, non-reversible identifier without logging the client IP."""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        _client_ip(request).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]


def rate_limited(request, scope, limit, window_seconds):
    """Increment a shared cache counter and return True after the allowed limit."""
    key_material = f"{scope}:{client_fingerprint(request)}"
    cache_key = f"patient-rate:{hashlib.sha256(key_material.encode('utf-8')).hexdigest()}"

    if cache.add(cache_key, 1, timeout=window_seconds):
        return False

    try:
        request_count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=window_seconds)
        request_count = 1
    return request_count > limit


def audit_patient_api_request(request, status_code):
    if not request.path.startswith("/api/patient/"):
        return

    resolver_match = getattr(request, "resolver_match", None)
    route_name = resolver_match.url_name if resolver_match and resolver_match.url_name else "unresolved"
    if status_code == 429:
        outcome = "rate_limited"
    elif status_code in (401, 403):
        outcome = "denied"
    elif status_code >= 500:
        outcome = "error"
    elif status_code >= 400:
        outcome = "rejected"
    else:
        outcome = "success"

    event = {
        "client_id": client_fingerprint(request),
        "event": "patient_api_request",
        "method": request.method,
        "outcome": outcome,
        "route": route_name,
        "status": int(status_code),
    }
    message = json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    log_method = audit_logger.warning if status_code >= 400 else audit_logger.info
    log_method(message)
