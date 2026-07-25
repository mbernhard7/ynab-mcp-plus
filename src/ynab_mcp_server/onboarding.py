"""Public onboarding page and access-request endpoint.

Served at ``/`` on the HTTP app. Explains how to connect an MCP client, how to
find your YNAB user ID, and (when ``SLACK_WEBHOOK_URL`` is configured) lets a
prospective user request access; the request is forwarded as a Slack message to
the operator, who can then add the user ID to ``YNAB_ALLOWED_USER_IDS``.

The "find my user ID" helper runs entirely in the browser: the PAT is sent by
the visitor's browser straight to api.ynab.com and never reaches this server.
"""

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .settings import settings

_MAX_FIELD = 200
_MAX_REQUEST = 2000


def _page_html() -> str:
    mcp_url = (settings.public_url or "").rstrip("/") + "/mcp"
    form_enabled = bool(settings.slack_webhook_url)
    form_note = (
        ""
        if form_enabled
        else "<p class='muted'>Access requests are not enabled on this server; contact the operator directly.</p>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YNAB MCP Server</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 720px; margin: 0 auto; padding: 2rem 1.25rem 4rem; line-height: 1.6; }}
  h1 {{ margin-bottom: 0.25rem; }}
  h2 {{ margin-top: 2.5rem; border-bottom: 1px solid rgba(128,128,128,.35); padding-bottom: .25rem; }}
  code, pre {{ background: rgba(128,128,128,.15); border-radius: 6px; }}
  code {{ padding: .1rem .35rem; }}
  pre {{ padding: .75rem 1rem; overflow-x: auto; }}
  .muted {{ opacity: .7; font-size: .95rem; }}
  label {{ display: block; margin-top: 1rem; font-weight: 600; }}
  input, textarea {{ width: 100%; box-sizing: border-box; padding: .55rem .7rem; margin-top: .3rem;
          border: 1px solid rgba(128,128,128,.5); border-radius: 8px; font: inherit;
          background: transparent; color: inherit; }}
  textarea {{ min-height: 6rem; resize: vertical; }}
  button {{ margin-top: 1.25rem; padding: .6rem 1.4rem; border: 0; border-radius: 8px;
           background: #3b82f6; color: #fff; font: inherit; font-weight: 600; cursor: pointer; }}
  button:disabled {{ opacity: .5; cursor: default; }}
  .ok {{ color: #16a34a; font-weight: 600; }}
  .err {{ color: #dc2626; font-weight: 600; }}
  .hp {{ position: absolute; left: -9999px; }}
</style>
</head>
<body>
<h1>YNAB MCP Server</h1>
<p class="muted">Talk to your YNAB budget from Claude or any MCP client.</p>

<h2>1. Find your YNAB user ID</h2>
<p>Create a Personal Access Token at
<a href="https://app.ynab.com/settings/developer" target="_blank" rel="noopener">app.ynab.com &rarr; Developer Settings</a>,
then paste it here. The token is sent by <em>your browser directly to YNAB</em> — it never
touches this server, and you can delete it right after.</p>
<label for="pat">Personal Access Token</label>
<input id="pat" autocomplete="off" placeholder="paste token">
<button id="lookup">Look up my user ID</button>
<p id="uid-out"></p>
<p class="muted">Prefer the terminal? <code>curl -H "Authorization: Bearer YOUR_TOKEN" https://api.ynab.com/v1/user</code></p>

<h2>2. Request access</h2>
{form_note}
<form id="req" {"" if form_enabled else "hidden"}>
  <label for="name">Name</label>
  <input id="name" name="name" required maxlength="{_MAX_FIELD}">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" required maxlength="{_MAX_FIELD}">
  <label for="user_id">YNAB user ID</label>
  <input id="user_id" name="user_id" required maxlength="{_MAX_FIELD}" placeholder="from step 1">
  <label for="request">What do you want to use it for?</label>
  <textarea id="request" name="request" maxlength="{_MAX_REQUEST}"></textarea>
  <input class="hp" type="text" name="website" tabindex="-1" autocomplete="off">
  <button type="submit">Request access</button>
  <p id="req-out"></p>
</form>

<h2>3. Connect your client</h2>
<p>Once you're approved, add a custom connector / remote MCP server with this URL:</p>
<pre>{mcp_url}</pre>
<p>Your client will walk you through "Sign in with YNAB" automatically.</p>

<script>
document.getElementById('lookup').addEventListener('click', async () => {{
  const out = document.getElementById('uid-out');
  const pat = document.getElementById('pat').value.trim();
  if (!pat) {{ out.innerHTML = '<span class="err">Paste a token first.</span>'; return; }}
  out.textContent = 'Looking up…';
  try {{
    const r = await fetch('https://api.ynab.com/v1/user', {{ headers: {{ Authorization: 'Bearer ' + pat }} }});
    if (!r.ok) throw new Error(r.status);
    const j = await r.json();
    const id = j.data.user.id;
    out.innerHTML = 'Your YNAB user ID: <code>' + id + '</code>';
    const f = document.getElementById('user_id');
    if (f && !f.value) f.value = id;
  }} catch (e) {{
    out.innerHTML = '<span class="err">Lookup failed — check the token.</span>';
  }}
}});
const form = document.getElementById('req');
if (form) form.addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const out = document.getElementById('req-out');
  const btn = form.querySelector('button');
  btn.disabled = true; out.textContent = 'Sending…';
  try {{
    const data = Object.fromEntries(new FormData(form).entries());
    const r = await fetch('/onboard/request', {{
      method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(data)
    }});
    if (!r.ok) throw new Error(r.status);
    out.innerHTML = '<span class="ok">Request sent! You\\'ll hear back once you\\'re approved.</span>';
    form.reset();
  }} catch (e) {{
    out.innerHTML = '<span class="err">Failed to send — try again later.</span>';
    btn.disabled = false;
  }}
}});
</script>
</body>
</html>"""


async def onboarding_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_page_html())


async def handle_onboard_request(request: Request) -> JSONResponse:
    webhook = settings.slack_webhook_url
    if not webhook:
        return JSONResponse({"error": "access requests are not enabled"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    # Honeypot: real users never fill this hidden field; silently accept bots.
    if (body.get("website") or "").strip():
        return JSONResponse({"ok": True})

    name = (body.get("name") or "").strip()[:_MAX_FIELD]
    email = (body.get("email") or "").strip()[:_MAX_FIELD]
    user_id = (body.get("user_id") or "").strip()[:_MAX_FIELD]
    request_text = (body.get("request") or "").strip()[:_MAX_REQUEST]
    if not name or not email or not user_id:
        return JSONResponse(
            {"error": "name, email, and user_id are required"}, status_code=400
        )

    message = (
        ":wave: *New YNAB MCP access request*\n"
        f"• *Name:* {name}\n"
        f"• *Email:* {email}\n"
        f"• *YNAB user ID:* `{user_id}`\n"
        f"• *Request:* {request_text or '(none)'}\n\n"
        "To approve, add the user ID to `YNAB_ALLOWED_USER_IDS` on the ynab-mcp service."
    )
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(webhook, json={"text": message})
            resp.raise_for_status()
    except httpx.HTTPError:
        return JSONResponse({"error": "failed to deliver request"}, status_code=502)

    return JSONResponse({"ok": True})
