"""Describes the device behind a request, for security notification emails only.

Both values come from the client and can be faked, so they are informational. They
are put in the email and never stored or logged.
"""

import ipaddress

from user_agents import parse

UNKNOWN_DEVICE = "Unknown device"
UNKNOWN_IP = "Unknown"
MAX_DEVICE_LENGTH = 100


def describe_user_agent(user_agent: str) -> str:
    """Turns a User-Agent header into "Chrome on Windows"; unrecognised values become "Unknown device"."""
    parsed = parse(user_agent or "")
    browser, os_name = parsed.browser.family, parsed.os.family
    if browser == "Other" and os_name == "Other":
        text = UNKNOWN_DEVICE
    elif os_name == "Other":
        text = browser
    elif browser == "Other":
        text = os_name
    else:
        text = f"{browser} on {os_name}"
    # Drop line breaks and other control characters, then keep it short.
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return text[:MAX_DEVICE_LENGTH] or UNKNOWN_DEVICE


def client_ip(meta: dict) -> str:
    """The caller's IP: the last X-Forwarded-For entry (added by Render's proxy), else REMOTE_ADDR."""
    forwarded = meta.get("HTTP_X_FORWARDED_FOR", "")
    candidate = forwarded.split(",")[-1].strip() if forwarded else meta.get("REMOTE_ADDR", "")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return UNKNOWN_IP


def device_info(request) -> dict:
    """{"device": ..., "ip": ...} for the request, safe to put in a plain-text email."""
    return {
        "device": describe_user_agent(request.META.get("HTTP_USER_AGENT", "")),
        "ip": client_ip(request.META),
    }
