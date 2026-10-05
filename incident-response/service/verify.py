"""The responder's own independent check, after the agent claims to be done -
kept separate from whatever the agent itself reports, so a confident-but-wrong
agent response can't be the only signal that an incident is actually resolved.
"""

from __future__ import annotations

import httpx


def verify_endpoint(base_url: str, path: str | None) -> dict:
    if not path:
        return {"checked": False, "reason": "no endpoint on the alert to verify"}
    if "{" in path:
        return {
            "checked": False,
            "reason": f"alert endpoint {path!r} is a route template, not a concrete URL; "
            "relying on the agent's own re-issue of the failing request",
        }
    url = f"{base_url}{path}"
    try:
        response = httpx.get(url, timeout=10)
        return {
            "checked": True,
            "url": url,
            "status_code": response.status_code,
            "passed": response.status_code < 500,
        }
    except Exception as exc:
        return {"checked": True, "url": url, "error": repr(exc), "passed": False}
