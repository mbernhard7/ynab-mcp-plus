"""Slack approve-button flow for onboarding requests.

The onboarding form posts a Slack message with an Approve button. Clicking it
sends an interaction payload to ``/slack/interact`` (verified against
``SLACK_SIGNING_SECRET``). The handler then:

1. appends the requester's YNAB user ID to ``YNAB_ALLOWED_USER_IDS`` on this
   very Cloud Run service via the Cloud Run Admin API (the runtime service
   account already deploys this service from Cloud Build, so it has the needed
   permissions) and waits for the new revision to roll out;
2. emails the requester that they're approved (when SMTP is configured);
3. updates the original Slack message with the outcome.

Slack requires an ACK within 3 seconds, so the work runs as a background task
and reports back through the interaction's ``response_url``.
"""

import asyncio
import hashlib
import hmac
import json
import os
import smtplib
import time
from email.message import EmailMessage
from typing import Optional

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .settings import settings

_METADATA = "http://metadata.google.internal/computeMetadata/v1"
_MD_HEADERS = {"Metadata-Flavor": "Google"}
_ALLOWLIST_ENV = "YNAB_ALLOWED_USER_IDS"

# Keep references to in-flight approval tasks so they aren't garbage collected.
_tasks: set[asyncio.Task] = set()


# ------------------------------------------------------------------ #
# GCP context (project / region / service / access token)
# ------------------------------------------------------------------ #
async def _metadata(path: str) -> str:
    async with httpx.AsyncClient(timeout=5) as http:
        resp = await http.get(f"{_METADATA}/{path}", headers=_MD_HEADERS)
        resp.raise_for_status()
        return resp.text


async def gcp_context() -> tuple[str, str, str]:
    """(project_id, region, service_name), with fallbacks for local dev."""
    service = os.environ.get("K_SERVICE", "ynab-mcp")
    try:
        project = await _metadata("project/project-id")
        region = (await _metadata("instance/region")).rsplit("/", 1)[-1]
    except httpx.HTTPError:
        project, region = "ynab-mcp-503313", "us-central1"
    return project, region, service


async def _access_token() -> str:
    data = json.loads(await _metadata("instance/service-accounts/default/token"))
    return data["access_token"]


# ------------------------------------------------------------------ #
# Allowlist update via the Cloud Run Admin API
# ------------------------------------------------------------------ #
async def add_user_to_allowlist(user_id: str) -> tuple[str, bool]:
    """Append ``user_id`` to the service's allowlist env var.

    Returns (new_allowlist, changed). Waits for the rollout to finish.
    """
    project, region, service = await gcp_context()
    token = await _access_token()
    base = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/services/{service}"
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(base, headers=headers)
        resp.raise_for_status()
        svc = resp.json()

        container = svc["template"]["containers"][0]
        env = container.setdefault("env", [])
        entry = next((e for e in env if e.get("name") == _ALLOWLIST_ENV), None)
        current = [
            part.strip()
            for part in (entry.get("value", "") if entry else "").split(",")
            if part.strip()
        ]
        if user_id in current:
            return ",".join(current), False
        current.append(user_id)
        new_value = ",".join(current)
        if entry is None:
            env.append({"name": _ALLOWLIST_ENV, "value": new_value})
        else:
            entry["value"] = new_value
            entry.pop("valueSource", None)

        resp = await http.patch(base, headers=headers, json=svc)
        resp.raise_for_status()
        operation = resp.json()["name"]

        # Poll the rollout so the caller can promise the user access works.
        op_url = f"https://run.googleapis.com/v2/{operation}"
        for _ in range(60):
            op = (await http.get(op_url, headers=headers)).json()
            if op.get("done"):
                if "error" in op:
                    raise RuntimeError(f"Cloud Run update failed: {op['error']}")
                break
            await asyncio.sleep(3)

    return new_value, True


# ------------------------------------------------------------------ #
# Approval email
# ------------------------------------------------------------------ #
def _send_email_sync(to_email: str, name: str) -> None:
    host = settings.smtp_host
    user = settings.smtp_user
    password = settings.smtp_pass
    sender = settings.approval_email_from or user
    connect_url = (settings.public_url or "").rstrip("/")

    msg = EmailMessage()
    msg["Subject"] = "You're approved: YNAB MCP server access"
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(
        f"Hi {name},\n\n"
        "Your access request to the YNAB MCP server was approved. You can now "
        "connect your MCP client:\n\n"
        f"  1. Add a remote MCP server / custom connector with URL: {connect_url}/mcp\n"
        '  2. Sign in with YNAB when prompted.\n\n'
        f"Setup instructions: {connect_url}/\n\n"
        "Happy budgeting!"
    )
    with smtplib.SMTP(host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


async def send_approval_email(to_email: str, name: str) -> Optional[str]:
    """Send the approval email. Returns an error description, or None on success."""
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_pass):
        return "email not configured (set SMTP_HOST/SMTP_USER/SMTP_PASS)"
    try:
        await asyncio.to_thread(_send_email_sync, to_email, name)
        return None
    except Exception as exc:  # smtplib raises a small zoo of exception types
        return f"email failed: {exc}"


# ------------------------------------------------------------------ #
# Slack interaction endpoint
# ------------------------------------------------------------------ #
def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    secret = settings.slack_signing_secret
    if not secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    basestring = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _run_approval(action_value: dict, response_url: str, approver: str) -> None:
    user_id = action_value["user_id"]
    email = action_value.get("email", "")
    name = action_value.get("name", "there")

    lines = []
    try:
        new_list, changed = await add_user_to_allowlist(user_id)
        lines.append(
            f":white_check_mark: *Approved by {approver}*"
            + (" (was already on the allowlist)" if not changed else "")
        )
        lines.append(f"Allowlist is now: `{new_list}`")
    except Exception as exc:
        lines.append(f":x: *Approval by {approver} FAILED to update the allowlist:* {exc}")
        lines.append("Add the user manually with the command from the original request.")
        await _post_response(response_url, "\n".join(lines))
        return

    if email:
        email_error = await send_approval_email(email, name)
        if email_error:
            lines.append(f":email: Could not notify {email} — {email_error}. Email them manually.")
        else:
            lines.append(f":email: Approval email sent to {email}.")

    await _post_response(response_url, "\n".join(lines))


async def _post_response(response_url: str, text: str) -> None:
    payload = {
        "replace_original": False,
        "response_type": "in_channel",
        "text": text,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            await http.post(response_url, json=payload)
    except httpx.HTTPError:
        pass  # nothing left to report to


async def handle_slack_interact(request: Request) -> Response:
    if not settings.slack_signing_secret:
        return JSONResponse({"error": "interactivity is not configured"}, status_code=503)

    body = await request.body()
    if not _verify_slack_signature(
        body,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        request.headers.get("X-Slack-Signature", ""),
    ):
        return JSONResponse({"error": "bad signature"}, status_code=401)

    try:
        from urllib.parse import parse_qs

        payload = json.loads(parse_qs(body.decode())["payload"][0])
        action = payload["actions"][0]
        if action.get("action_id") != "approve_access":
            return Response(status_code=200)
        action_value = json.loads(action["value"])
        response_url = payload["response_url"]
        approver = payload.get("user", {}).get("username") or payload.get("user", {}).get(
            "name", "unknown"
        )
    except (KeyError, IndexError, ValueError):
        return JSONResponse({"error": "malformed payload"}, status_code=400)

    task = asyncio.create_task(_run_approval(action_value, response_url, approver))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    # Slack needs a 200 within 3 seconds; the outcome arrives via response_url.
    return Response(status_code=200)


# ------------------------------------------------------------------ #
# Slack message construction (used by the onboarding form handler)
# ------------------------------------------------------------------ #
async def build_request_message(
    name: str, email: str, user_id: str, request_text: str
) -> dict:
    project, region, service = await gcp_context()
    current = settings.allowed_user_ids or set()
    merged = ",".join(sorted(current | {user_id}))
    fallback_cmd = (
        f"gcloud run services update {service} --region {region} --project {project} "
        f"--update-env-vars {_ALLOWLIST_ENV}={merged}"
    )
    details = (
        ":wave: *New YNAB MCP access request*\n"
        f"• *Name:* {name}\n"
        f"• *Email:* {email}\n"
        f"• *YNAB user ID:* `{user_id}`\n"
        f"• *Request:* {request_text or '(none)'}"
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": details}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "approve_access",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "value": json.dumps({"user_id": user_id, "email": email, "name": name}),
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Approve access?"},
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Add `{user_id}` to the allowlist and email {email}?",
                        },
                        "confirm": {"type": "plain_text", "text": "Approve"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"Manual fallback: `{fallback_cmd}`"}
            ],
        },
    ]
    return {"text": f"New YNAB MCP access request from {name} ({email})", "blocks": blocks}
