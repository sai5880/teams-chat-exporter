import csv
import io
import json
import os
from datetime import datetime, timezone
from functools import wraps

import msal
import requests
from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, send_file, session, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "replace-this-for-local-development")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

CLIENT_ID = os.getenv("MICROSOFT_CLIENT_ID", "")
TENANT = os.getenv("MICROSOFT_TENANT", "organizations")
CLIENT_SECRET = os.getenv("MICROSOFT_CLIENT_SECRET", "")
REDIRECT_PATH = "/getAToken"
REDIRECT_URI = os.getenv("REDIRECT_URI", f"http://localhost:5000{REDIRECT_PATH}")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT}"
SCOPES = ["User.Read", "Chat.Read"]
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


def build_msal_app(cache=None):
    config = {"client_id": CLIENT_ID, "authority": AUTHORITY}
    if CLIENT_SECRET:
        config["client_credential"] = CLIENT_SECRET
    if not CLIENT_SECRET:
        raise RuntimeError("MICROSOFT_CLIENT_SECRET is required for this Flask web-app sample.")
    return msal.ConfidentialClientApplication(
        **config, token_cache=cache
    )


def token_cache():
    # Simple per-browser-session cache. For a personal local-only app this is
    # convenient; production deployments should use a protected server-side cache.
    cache = msal.SerializableTokenCache()
    serialized = session.get("token_cache")
    if serialized:
        cache.deserialize(serialized)
    return cache


def save_cache(cache):
    if cache.has_state_changed:
        session["token_cache"] = cache.serialize()


def get_access_token():
    if not CLIENT_ID:
        return None
    cache = token_cache()
    client = build_msal_app(cache)
    accounts = client.get_accounts()
    if accounts:
        result = client.acquire_token_silent(SCOPES, account=accounts[0])
        save_cache(cache)
        if result and "access_token" in result:
            return result["access_token"]
    return None


def graph_get(url, token, params=None):
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params,
        timeout=60,
    )
    if response.status_code == 401:
        session.pop("token_cache", None)
    if not response.ok:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Microsoft Graph returned HTTP {response.status_code}: {detail}")
    return response.json()


def get_all_pages(url, token, params=None):
    items = []
    first = True
    while url:
        payload = graph_get(url, token, params=params if first else None)
        first = False
        items.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink")
    return items


@app.route("/")
def index():
    token = get_access_token()
    if token:
        return redirect(url_for("chats"))
    return render_template("index.html", configured=bool(CLIENT_ID))


@app.route("/login")
def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        return render_template("index.html", configured=False,
                               error="Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET in your .env file first.")
    cache = token_cache()
    client = build_msal_app(cache)
    flow = client.initiate_auth_code_flow(SCOPES, redirect_uri=REDIRECT_URI)
    session["auth_flow"] = flow
    save_cache(cache)
    return redirect(flow["auth_uri"])


@app.route(REDIRECT_PATH)
def authorized():
    flow = session.get("auth_flow")
    if not flow:
        return redirect(url_for("index"))
    cache = token_cache()
    client = build_msal_app(cache)
    try:
        result = client.acquire_token_by_auth_code_flow(flow, request.args)
    except ValueError:
        return render_template("index.html", configured=True,
                               error="Sign-in response could not be validated. Please try again.")
    if "access_token" not in result:
        return render_template("index.html", configured=True,
                               error=result.get("error_description", result.get("error", "Sign-in failed.")))
    save_cache(cache)
    return redirect(url_for("chats"))


@app.route("/chats")
def chats():
    token = get_access_token()
    if not token:
        return redirect(url_for("login"))
    try:
        me = graph_get(f"{GRAPH_ROOT}/me", token)
        # $top is a page size, not a total limit; get_all_pages follows nextLink.
        chat_items = get_all_pages(
            f"{GRAPH_ROOT}/me/chats",
            token,
            params={"$top": "50", "$orderby": "lastMessagePreview/createdDateTime desc"},
        )
        chat_items.sort(
            key=lambda c: (c.get("lastMessagePreview") or {}).get("createdDateTime", ""),
            reverse=True,
        )
        return render_template("chats.html", chats=chat_items, me=me, error=None)
    except RuntimeError as exc:
        return render_template("chats.html", chats=[], me={}, error=str(exc))


@app.route("/chats/<path:chat_id>")
def chat_detail(chat_id):
    token = get_access_token()
    if not token:
        return redirect(url_for("login"))
    try:
        chat = graph_get(f"{GRAPH_ROOT}/chats/{chat_id}", token)
        messages = get_all_pages(
            f"{GRAPH_ROOT}/chats/{chat_id}/messages",
            token,
            params={"$top": "50", "$orderby": "createdDateTime desc"},
        )
        messages.sort(key=lambda m: m.get("createdDateTime", ""))
        return render_template("messages.html", chat=chat, messages=messages, error=None)
    except RuntimeError as exc:
        return render_template("messages.html", chat={"id": chat_id}, messages=[], error=str(exc))


def normalized_message(message):
    body = message.get("body") or {}
    sender = message.get("from") or {}
    user = sender.get("user") or {}
    app_identity = sender.get("application") or {}
    return {
        "createdDateTime": message.get("createdDateTime", ""),
        "lastModifiedDateTime": message.get("lastModifiedDateTime", ""),
        "sender": user.get("displayName") or app_identity.get("displayName") or "Unknown",
        "messageType": message.get("messageType", ""),
        "subject": message.get("subject", ""),
        "bodyContentType": body.get("contentType", ""),
        "body": body.get("content", ""),
        "id": message.get("id", ""),
        "replyToId": message.get("replyToId", ""),
        "webUrl": message.get("webUrl", ""),
    }


def retrieve_messages(chat_id):
    token = get_access_token()
    if not token:
        abort(401)
    messages = get_all_pages(
        f"{GRAPH_ROOT}/chats/{chat_id}/messages",
        token,
        params={"$top": "50", "$orderby": "createdDateTime desc"},
    )
    messages.sort(key=lambda m: m.get("createdDateTime", ""))
    return [normalized_message(m) for m in messages]


@app.route("/export/<fmt>/<path:chat_id>")
def export_chat(fmt, chat_id):
    if fmt not in {"json", "csv"}:
        abort(404)
    try:
        messages = retrieve_messages(chat_id)
    except RuntimeError as exc:
        return str(exc), 502

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if fmt == "json":
        data = json.dumps(messages, ensure_ascii=False, indent=2).encode("utf-8")
        return send_file(io.BytesIO(data), mimetype="application/json",
                         as_attachment=True, download_name=f"teams_chat_{stamp}.json")
    output = io.StringIO(newline="")
    fieldnames = list(messages[0].keys()) if messages else [
        "createdDateTime", "lastModifiedDateTime", "sender", "messageType",
        "subject", "bodyContentType", "body", "id", "replyToId", "webUrl"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(messages)
    return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")),
                     mimetype="text/csv", as_attachment=True,
                     download_name=f"teams_chat_{stamp}.csv")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
