"""Export a Teams (personal) chat to JSON/CSV, including shared files and images.

Teams Free and personal Microsoft accounts cannot use Microsoft Graph
(`/chats/{id}/messages` is "not supported" for delegated personal accounts),
so this tool replays the Teams web client's own API with your signed-in session.

Workflow:
  1. `login`  - one human sign-in. No need to open a chat or scroll.
  2. `list`   - show chats.
  3. `export` - page the full history and download media.

Every command supports `--json` for AI agents and automation.
"""

import argparse
import base64
import csv
import io
import json
import mimetypes
import re
import sys
import time
import urllib.parse
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / ".browser_profile"
CAPTURE_DIR = ROOT / "capture"
EXPORT_DIR = ROOT / "exports"
SESSION_PATH = CAPTURE_DIR / "session.json"
LOG_PATH = CAPTURE_DIR / "run.log"
INTERESTING = re.compile(r"chatsvc|/messages|/conversations|/chats/", re.I)
MAX_BODY = 3_000_000
MAX_ENTRIES = 300

API_ROOT = "https://teams.live.com/api/chatsvc/consumer/v1"
VIEW = "msnp24Equivalent|supportsMessageProperties|supportsExtendedHistory"
PAGE_SIZE = 200
MEDIA_HOSTS = (
    "teams.live.com", "asm.skype.com", "skype.com", "sharepoint.com", "sharepoint.us",
    "onedrive.com", "1drv.ms", "live.com", "microsoft.com", "office.com", "office.net",
    "office365.com", "svc.ms",
)
AUTH_HOSTS = ("teams.live.com", "skype.com")

JSON_MODE = False


def info(message):
    print(message, file=sys.stderr if JSON_MODE else sys.stdout, flush=True)


def log(message):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    CAPTURE_DIR.mkdir(exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def local_timezone():
    offset = datetime.now().astimezone().utcoffset()
    minutes = int(offset.total_seconds()) // 60 if offset else 0
    sign = "+" if minutes >= 0 else "-"
    hours, mins = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours:02d}:{mins:02d}"


HEADERS_BASE = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
    ),
    "clientinfo": f"os=windows; clientName=skypeteams; timezone={local_timezone()}",
    "behavioroverride": "redirectAs404",
}


def jwt_expiry(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat()
    except Exception:
        return None


def save_session(token, cookies):
    CAPTURE_DIR.mkdir(exist_ok=True)
    payload = {
        "token": token,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "expiresAt": jwt_expiry(token),
        "cookies": [
            {key: cookie.get(key) for key in ("name", "value", "domain", "path", "expires", "secure")}
            for cookie in cookies
        ],
    }
    SESSION_PATH.write_text(json.dumps(payload), encoding="utf-8")


def load_session():
    if SESSION_PATH.exists():
        payload = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        if payload.get("token"):
            return payload
    legacy = CAPTURE_DIR / "api_capture.json"
    if legacy.exists():
        for entry in json.loads(legacy.read_text(encoding="utf-8")):
            headers = {k.lower(): v for k, v in (entry.get("request_headers") or {}).items()}
            auth = headers.get("authentication", "")
            if "skypetoken=" in auth:
                return {"token": auth.split("skypetoken=", 1)[1].split(";")[0].strip(), "cookies": []}
    return None


def host_allowed(url, suffixes):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def cookies_for(cookies, url):
    host = (urlsplit(url).hostname or "").lower()
    jar = {}
    for cookie in cookies or []:
        domain = (cookie.get("domain") or "").lstrip(".").lower()
        if host == domain or host.endswith("." + domain):
            jar[cookie.get("name")] = cookie.get("value")
    return jar


def headers_for(token, url):
    headers = dict(HEADERS_BASE)
    headers["accept"] = "application/json, text/plain, */*"
    if host_allowed(url, AUTH_HOSTS):
        headers["authentication"] = f"skypetoken={token}"
    return headers


def api_get(session, url=None, params=None):
    token = session["token"]
    target = url or f"{API_ROOT}/users/ME/conversations"
    response = requests.get(
        target,
        headers=headers_for(token, target),
        params=params,
        timeout=60,
    )
    if response.status_code == 401:
        raise SessionExpired("Teams rejected the saved token (401). Run `login` again.")
    if not response.ok:
        raise RuntimeError(f"Teams API returned HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


class SessionExpired(RuntimeError):
    pass


def rewrite_link(link):
    return (link or "").replace("https://msgapi.teams.live.com/v1", f"{API_ROOT}")


def fetch_conversations(session):
    payload = api_get(session, params={"pageSize": 100, "startTime": 0, "view": VIEW})
    conversations = payload.get("conversations") or []
    seen = {c.get("id") for c in conversations}
    link = rewrite_link((payload.get("_metadata") or {}).get("forwardLink"))
    while link:
        payload = api_get(session, link)
        batch = payload.get("conversations") or []
        fresh = [c for c in batch if c.get("id") not in seen]
        if not fresh:
            break
        for conversation in fresh:
            seen.add(conversation.get("id"))
        conversations.extend(fresh)
        link = rewrite_link((payload.get("_metadata") or {}).get("forwardLink"))
    return conversations


def conversation_title(conversation):
    props = conversation.get("threadProperties") or {}
    return (props.get("topic") or props.get("spaceThreadTopic") or "").strip()


def fetch_history(session, conversation_id, max_pages=500):
    quoted = urllib.parse.quote(conversation_id, safe="")
    url = f"{API_ROOT}/users/ME/conversations/{quoted}/messages"
    payload = api_get(session, url, {"pageSize": PAGE_SIZE, "view": VIEW})
    messages = {}
    pages = 0
    while payload:
        pages += 1
        batch = payload.get("messages") or []
        fresh = 0
        for message in batch:
            key = message.get("id")
            if key and key not in messages:
                messages[key] = message
                fresh += 1
        info(f"  page {pages}: {len(messages)} messages")
        link = rewrite_link((payload.get("_metadata") or {}).get("backwardLink"))
        if not fresh or not link or pages >= max_pages:
            break
        payload = api_get(session, link)
    return list(messages.values())


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "br":
            self.parts.append("\n")
        elif tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        elif tag == "img" and attributes.get("src"):
            self.images.append({"src": attributes["src"], "alt": attributes.get("alt") or "",
                                "title": attributes.get("title") or ""})

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join([line for line in lines if line])


def clean_text(content):
    if not content:
        return ""
    if "<" not in content:
        return content.strip()
    parser = TextExtractor()
    parser.feed(content)
    parser.close()
    text = parser.text()
    for link in dict.fromkeys(parser.links):
        if link and link not in text:
            text += f"\n{link}"
    return text.strip()


def summarize_call(content):
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return clean_text(content)[:500]
    parts = root.findall(".//part")
    duration = max((int(part.findtext("duration") or 0) for part in parts), default=0)
    minutes, seconds = divmod(duration // 1000, 60)
    hours, minutes = divmod(minutes, 60)
    length = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
    action = {"started": "Meeting started", "ended": "Meeting ended"}.get(root.tag, f"Call {root.tag}")
    return f"{action}: {length} ({len(parts)} participants)"


def keep_message(message, include_system, include_events):
    message_type = str(message.get("messagetype") or "")
    if message_type.startswith("ThreadActivity/"):
        return include_system
    if message_type == "Event/Call":
        return include_events
    return True


def parse_json_list(value):
    if not value:
        return []
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def resolve_media_url(src):
    if not src:
        return None
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("http"):
        return src
    for prefix in ("../objects/", "/objects/", "objects/"):
        if src.startswith(prefix):
            return f"{API_ROOT}/users/ME/objects/{src.split('objects/', 1)[1]}"
    if src.startswith("/v1/"):
        return f"https://teams.live.com/api/chatsvc/consumer{src}"
    if src.startswith("/api/"):
        return f"https://teams.live.com{src}"
    return None


def object_candidates(object_id):
    base = f"{API_ROOT}/users/ME/objects/{object_id}"
    return [
        f"{base}/views/imgo",
        f"{base}/views/imgpsh_fullsize_anim",
        f"{base}/views/original",
        base,
    ]


def collect_media_sources(message):
    properties = message.get("properties") or {}
    content = message.get("content") or ""
    sources = []
    for item in parse_json_list(properties.get("files")):
        if not isinstance(item, dict):
            continue
        url = item.get("fileUrl") or item.get("url") or item.get("downloadUrl") or item.get("objectUrl")
        name = item.get("fileName") or item.get("title") or ""
        if url:
            sources.append({"kind": "file", "url": url, "name": name})
    parser = TextExtractor()
    if "<" in content:
        try:
            parser.feed(content)
            parser.close()
        except Exception:
            pass
    for image in parser.images:
        url = resolve_media_url(image["src"])
        name = image.get("alt") or image.get("title") or ""
        if url:
            sources.append({"kind": "image", "url": url, "name": name})
    for reference in message.get("amsreferences") or []:
        object_id = reference if isinstance(reference, str) else (reference or {}).get("id")
        if not object_id:
            continue
        if any(object_id in (source.get("url") or "") for source in sources):
            continue
        sources.append({"kind": "image", "objectId": object_id, "name": ""})
    unique, seen = [], set()
    for source in sources:
        key = source.get("url") or source.get("objectId")
        if key and key not in seen:
            seen.add(key)
            unique.append(source)
    return unique


def sanitize_filename(name, fallback="attachment"):
    name = urllib.parse.unquote(name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return ""
    if len(name) > 100:
        stem, dot, extension = name.rpartition(".")
        name = (stem[:100 - len(extension) - 1] + "." + extension) if dot and len(extension) < 10 else name[:100]
    return name or fallback


def filename_from_response(response, hint, kind):
    if hint:
        return sanitize_filename(hint)
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition)
    if match:
        return sanitize_filename(match.group(1))
    path = urlsplit(response.url).path
    base = sanitize_filename(Path(urllib.parse.unquote(path)).name)
    if base and "." in base and len(base) > 4:
        return base
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    extension = mimetypes.guess_extension(content_type) or (".jpg" if kind == "image" else "")
    stem = base or ("image" if kind == "image" else "file")
    return sanitize_filename(stem + ("" if stem.endswith(extension) else extension))


def unique_path(directory, filename):
    path = directory / filename
    counter = 1
    while path.exists():
        stem, dot, extension = filename.rpartition(".")
        if dot:
            path = directory / f"{stem}-{counter}.{extension}"
        else:
            path = directory / f"{filename}-{counter}"
        counter += 1
    return path


COOKIELESS_HOSTS = ("teams.live.com", "msgapi.teams.live.com")
SKIP_HOSTS = ("cdn.office.net", "cdn.live.net")


def request_cookies(session, url):
    host = (urlsplit(url).hostname or "").lower()
    if any(host == item or host.endswith("." + item) for item in COOKIELESS_HOSTS):
        return {}
    return cookies_for(session.get("cookies"), url)


def ensure_extension(name, content_type):
    if name and not Path(name).suffix:
        extension = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
        if extension:
            return name + extension
    return name


GENERIC_MEDIA_NAMES = {"image", "img", "photo", "picture", "attachment", "file", "download"}


def is_generic_name(name):
    stem = Path(name or "").stem.strip().lower()
    return not stem or stem in GENERIC_MEDIA_NAMES or stem.startswith("image")


def download_media(session, source, destination, hint_prefix="", cache=None):
    """Download one media item. Returns a manifest entry."""
    token = session["token"]
    entry = {
        "kind": source["kind"],
        "name": source.get("name") or "",
        "source": source.get("url") or "",
        "status": "failed",
        "path": "",
        "bytes": 0,
        "error": "",
    }
    candidates = [source["url"]] if source.get("url") else []
    if source.get("objectId"):
        candidates.extend(object_candidates(source["objectId"]))
    if not candidates:
        entry["error"] = "no url"
        return entry

    cache_key = source.get("url") or source.get("objectId")
    if cache is not None and cache_key in cache:
        cached = cache[cache_key]
        entry.update({"status": "reused", "path": cached["path"], "bytes": cached["bytes"],
                      "name": cached["name"]})
        return entry

    for url in candidates:
        host = (urlsplit(url).hostname or "").lower()
        if any(host == item or host.endswith("." + item) for item in SKIP_HOSTS):
            entry.update({"status": "skipped", "error": "emoji/sticker CDN asset"})
            return entry
        if not host_allowed(url, MEDIA_HOSTS):
            entry["error"] = f"blocked host: {urlsplit(url).hostname}"
            continue
        cookie_variants = [request_cookies(session, url)]
        if cookie_variants[0]:
            cookie_variants.append({})
        for cookies in cookie_variants:
            try:
                response = requests.get(
                    url,
                    headers=headers_for(token, url),
                    cookies=cookies,
                    timeout=90,
                    stream=True,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                entry["error"] = str(exc)[:200]
                continue
            if response.status_code in (401, 403):
                entry["error"] = f"HTTP {response.status_code} (sign-in required)"
                response.close()
                continue
            if not response.ok:
                entry["error"] = f"HTTP {response.status_code}"
                response.close()
                continue
            if not host_allowed(response.url, MEDIA_HOSTS):
                entry["error"] = f"redirected off allowlist: {urlsplit(response.url).hostname}"
                response.close()
                continue
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                entry["error"] = "got an HTML sign-in page instead of the file"
                response.close()
                continue
            if entry["name"] and not is_generic_name(entry["name"]):
                hint = entry["name"]
            elif entry["name"]:
                hint = hint_prefix + sanitize_filename(entry["name"])
            else:
                hint = hint_prefix
            name = filename_from_response(response, hint, source["kind"])
            name = ensure_extension(sanitize_filename(name), content_type)
            path = unique_path(destination, name)
            written = 0
            try:
                with path.open("wb") as handle:
                    for chunk in response.iter_content(65536):
                        handle.write(chunk)
                        written += len(chunk)
            except OSError as exc:
                entry["error"] = f"write failed: {exc}"
                path.unlink(missing_ok=True)
                response.close()
                continue
            response.close()
            if written == 0:
                entry["error"] = "empty response"
                path.unlink(missing_ok=True)
                continue
            entry.update({
                "status": "downloaded",
                "path": str(path.relative_to(destination.parent)).replace("\\", "/"),
                "bytes": written,
                "name": path.name,
                "source": url,
            })
            if cache is not None and cache_key:
                cache[cache_key] = dict(entry)
            return entry
    return entry


def normalized(message):
    properties = message.get("properties") or {}
    created = message.get("composetime") or message.get("originalarrivaltime") or ""
    timestamp_utc = timestamp_local = ""
    if created:
        try:
            parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
            timestamp_utc = parsed.astimezone(timezone.utc).isoformat()
            timestamp_local = parsed.astimezone().isoformat(timespec="seconds")
        except ValueError:
            timestamp_utc = created
    files = parse_json_list(properties.get("files"))
    attachments = [f.get("fileName") or f.get("title") or f.get("fileUrl", "")
                   for f in files if isinstance(f, dict)]
    reactions = parse_json_list(properties.get("emotions"))
    return {
        "timestampUtc": timestamp_utc,
        "timestampLocal": timestamp_local,
        "sender": message.get("imdisplayname") or "",
        "messageType": message.get("messagetype") or "",
        "contentType": message.get("contenttype") or "",
        "text": clean_text(message.get("content") or ""),
        "attachments": "; ".join(filter(None, attachments)),
        "reactions": "; ".join(
            f"{r.get('emotion') or r.get('key') or 'reaction'}:{r.get('count') or ''}".rstrip(":")
            for r in reactions if isinstance(r, dict)
        ),
        "messageId": message.get("id") or "",
        "sequenceId": message.get("sequenceId"),
        "threadId": message.get("conversationid") or "",
        "media": [],
        "rawContent": message.get("content") or "",
    }


def slugify(value, fallback="teams_chat"):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug[:60] or fallback


def write_exports(session, title, conversation_id, messages, out_dir, include_system, include_events,
                  download, make_zip):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_dir = Path(out_dir) / f"{slugify(title)}_{stamp}"
    media_dir = base_dir / "media"
    base_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(exist_ok=True)

    exported = []
    skipped = 0
    media_cache = {}
    for message in messages:
        if not keep_message(message, include_system, include_events):
            skipped += 1
            continue
        entry = normalized(message)
        if entry["messageType"] == "Event/Call":
            entry["text"] = summarize_call(entry["rawContent"])
        if download:
            sources = collect_media_sources(message)
            for index, source in enumerate(sources):
                stamp_part = entry["timestampUtc"][:10].replace("-", "") if entry["timestampUtc"] else "media"
                manifest = download_media(
                    session, source, media_dir,
                    hint_prefix=f"{stamp_part}_{entry['messageId']}_{index}_",
                    cache=media_cache,
                )
                manifest["messageId"] = entry["messageId"]
                entry["media"].append(manifest)
        exported.append(entry)

    exported.sort(key=lambda item: (item["timestampUtc"], item["messageId"]))
    for entry in exported:
        entry["mediaFiles"] = "; ".join(
            item["path"] for item in entry["media"] if item["status"] in ("downloaded", "reused"))


    json_path = base_dir / "messages.json"
    json_path.write_text(json.dumps({
        "chatTitle": title,
        "conversationId": conversation_id,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "totalMessages": len(exported),
        "messages": exported,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = base_dir / "messages.csv"
    fieldnames = ["timestampLocal", "timestampUtc", "sender", "messageType", "text",
                  "attachments", "mediaFiles", "reactions", "messageId", "threadId"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(exported)

    manifest_items = [
        {"messageId": m["messageId"], **item}
        for m in exported for item in m["media"]
    ]
    manifest_path = base_dir / "manifest.json"
    manifest_path.write_text(json.dumps({
        "chatTitle": title,
        "conversationId": conversation_id,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "media": manifest_items,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    downloaded = sum(1 for item in manifest_items if item["status"] in ("downloaded", "reused"))
    skipped_media = sum(1 for item in manifest_items if item["status"] == "skipped")
    failed = sum(1 for item in manifest_items if item["status"] == "failed")
    zip_path = None
    if make_zip:
        zip_path = base_dir.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(base_dir.rglob("*")):
                if file.is_file():
                    archive.write(file, file.relative_to(base_dir.parent).as_posix())

    senders = {}
    for item in exported:
        senders[item["sender"] or "(unknown)"] = senders.get(item["sender"] or "(unknown)", 0) + 1
    return {
        "ok": True,
        "chatTitle": title,
        "conversationId": conversation_id,
        "messages": len(exported),
        "skippedEvents": skipped,
        "participants": len(senders),
        "mediaDownloaded": downloaded,
        "mediaSkipped": skipped_media,
        "mediaFailed": failed,
        "firstMessage": exported[0]["timestampLocal"] if exported else None,
        "lastMessage": exported[-1]["timestampLocal"] if exported else None,
        "folder": str(base_dir),
        "json": str(json_path),
        "csv": str(csv_path),
        "manifest": str(manifest_path),
        "zip": str(zip_path) if zip_path else None,
    }


def launch_context(playwright):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        viewport={"width": 1400, "height": 950},
        args=["--disable-blink-features=AutomationControlled", "--disable-gpu",
              "--no-first-run", "--no-default-browser-check"],
    )


def capture_session(timeout, keep_open, start_url):
    CAPTURE_DIR.mkdir(exist_ok=True)
    flag = ROOT / "go.flag"
    if flag.exists():
        flag.unlink()
    found = {}

    def on_response(response):
        if "skypetoken=" not in (response.request.headers.get("authentication") or ""):
            return
        auth = response.request.headers["authentication"]
        found["token"] = auth.split("skypetoken=", 1)[1].split(";")[0].strip()

    with sync_playwright() as playwright:
        context = launch_context(playwright)
        page = context.pages[0] if context.pages else context.new_page()
        page.on("response", on_response)
        info(f"Opening {start_url} - sign in with your personal Microsoft account.")
        try:
            page.goto(start_url, wait_until="domcontentloaded")
        except Exception as exc:
            log(f"navigation warning: {exc}")

        started = time.time()
        noticed = False
        recoveries = {25: "reload", 55: "home", 90: "v2", 130: "reload"}
        while time.time() - started < timeout:
            if found.get("token"):
                break
            if page.is_closed():
                raise RuntimeError("Browser closed before a Teams session was detected.")
            elapsed = int(time.time() - started)
            if elapsed in recoveries:
                action = recoveries.pop(elapsed)
                try:
                    if action == "reload":
                        log("splash screen detected, reloading Teams")
                        page.reload(wait_until="domcontentloaded")
                    elif action == "home":
                        log("still stuck, reopening teams.live.com")
                        page.goto("https://teams.live.com/", wait_until="domcontentloaded")
                    else:
                        log("reopening the Teams web app")
                        page.goto(start_url, wait_until="domcontentloaded")
                except Exception as exc:
                    log(f"recovery failed: {exc}")
            if time.time() - started > 20 and not noticed:
                noticed = True
                info("Waiting for sign-in (the window closes by itself once Teams loads)...")
            time.sleep(1)

        if not found.get("token"):
            context.close()
            raise RuntimeError(
                f"No Teams session detected within {timeout}s. Sign in fully until the chat list appears, then retry."
            )

        cookies = context.cookies()
        save_session(found["token"], cookies)
        info("Session captured.")

        if keep_open:
            info("Debug mode: create go.flag to finish.")
            deadline = time.time() + 15 * 60
            while time.time() < deadline and not flag.exists() and not page.is_closed():
                time.sleep(1)
        else:
            page.wait_for_timeout(1500)
        try:
            context.close()
        except Exception:
            pass
    return {"ok": True, "expiresAt": jwt_expiry(found["token"]), "session": str(SESSION_PATH)}


def discover(wait_minutes, start_url):
    """Debug helper: capture API samples, cookies, storage and DOM."""
    CAPTURE_DIR.mkdir(exist_ok=True)
    flag = ROOT / "go.flag"
    if flag.exists():
        flag.unlink()
    captures, seen = [], set()

    def on_response(response):
        url = response.url
        if "teams" not in url or not INTERESTING.search(url):
            return
        key = (response.request.method, url)
        if key in seen or len(captures) >= MAX_ENTRIES:
            return
        seen.add(key)
        entry = {"at": datetime.now(timezone.utc).isoformat(),
                 "method": response.request.method, "url": url,
                 "status": response.status,
                 "request_headers": dict(response.request.headers),
                 "post_data": response.request.post_data}
        try:
            content_type = (response.headers or {}).get("content-type", "")
            if "json" in content_type.lower():
                text = response.text()
                entry["body"] = json.loads(text) if len(text) <= MAX_BODY else text[:MAX_BODY]
            else:
                entry["body_text"] = response.text()[:4000]
        except Exception as exc:
            entry["body_error"] = str(exc)
        captures.append(entry)
        log(f"captured {entry['method']} {response.status} {url[:160]}")

    with sync_playwright() as playwright:
        context = launch_context(playwright)
        page = context.pages[0] if context.pages else context.new_page()
        page.on("response", on_response)
        log(f"opening {start_url}")
        page.goto(start_url, wait_until="domcontentloaded")
        deadline = time.time() + wait_minutes * 60
        while time.time() < deadline:
            if flag.exists() or page.is_closed():
                break
            time.sleep(1)
        (CAPTURE_DIR / "api_capture.json").write_text(json.dumps(captures, indent=2), encoding="utf-8")
        try:
            (CAPTURE_DIR / "cookies.json").write_text(
                json.dumps(context.cookies(), indent=2), encoding="utf-8")
            storage = page.evaluate("""() => {
                const dump = (s) => { const o = {}; for (let i = 0; i < s.length; i++) {
                    const k = s.key(i); const v = s.getItem(k) || '';
                    o[k] = v.length < 5000 ? v : v.slice(0, 5000) + '...[truncated]'; } return o; };
                return {localStorage: dump(localStorage), sessionStorage: dump(sessionStorage)};
            }""")
            (CAPTURE_DIR / "storage.json").write_text(json.dumps(storage, indent=2), encoding="utf-8")
        except Exception as exc:
            log(f"extra dump failed: {exc}")
        context.close()
    log(f"saved {len(captures)} API captures")
    return {"ok": True, "captures": len(captures)}


def command_status():
    session = load_session()
    if not session:
        return {"ok": True, "session": False, "expiresAt": None,
                "hint": "Run `login` once, then this tool works without the browser."}
    expires = jwt_expiry(session["token"])
    expired = False
    if expires:
        expired = datetime.fromisoformat(expires) <= datetime.now(timezone.utc)
    return {"ok": True, "session": True, "expiresAt": expires, "expired": expired,
            "hint": "Run `login` again." if expired else None}


def command_list():
    session = load_session()
    if not session:
        raise RuntimeError("No saved session. Run `login` first.")
    conversations = fetch_conversations(session)
    conversations.sort(key=lambda c: (c.get("lastMessage") or {}).get("composetime", ""), reverse=True)
    chats = [{
        "title": conversation_title(conversation) or "(no title)",
        "id": conversation.get("id") or "",
        "lastMessage": (conversation.get("lastMessage") or {}).get("composetime", ""),
        "members": len(conversation.get("memberProperties") or {}),
    } for conversation in conversations]
    if JSON_MODE:
        return {"ok": True, "count": len(chats), "chats": chats}
    print(f"{len(chats)} chats:\n")
    for chat in chats:
        print(f"  {chat['title']}  [{chat['lastMessage'][:19]}]")
        print(f"    {chat['id']}")
    return None


def command_export(chat, out_dir, include_system, include_events, download, make_zip):
    session = load_session()
    if not session:
        raise RuntimeError("No saved session. Run `login` first.")
    conversations = fetch_conversations(session)
    if chat.startswith("19:"):
        matches = [c for c in conversations if c.get("id") == chat]
    else:
        needle = chat.lower()
        matches = [c for c in conversations if needle in conversation_title(c).lower()]
    if not matches:
        raise RuntimeError(f"No conversation matched {chat!r}. Run `list` to see titles.")
    if len(matches) > 1:
        titles = [f"{conversation_title(c)}  {c.get('id')}" for c in matches]
        raise RuntimeError("Multiple chats matched; pass a full id:\n  " + "\n  ".join(titles))
    conversation = matches[0]
    title = conversation_title(conversation) or "teams_chat"
    info(f"Exporting {title!r}")
    messages = fetch_history(session, conversation["id"])
    return write_exports(session, title, conversation["id"], messages, out_dir,
                         include_system, include_events, download, make_zip)


def main():
    global JSON_MODE
    parser = argparse.ArgumentParser(
        description="Export personal/free Microsoft Teams chats, files and images to JSON/CSV.")
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="one-time browser sign-in (no chat scrolling needed)")
    login.add_argument("--timeout", type=int, default=300, help="seconds to wait for sign-in")
    login.add_argument("--keep-open", action="store_true", help="keep the browser open until go.flag")
    login.add_argument("--url", default="https://teams.live.com/v2/")
    login.add_argument("--json", action="store_true")

    status = sub.add_parser("status", help="show whether a saved session is still valid")
    status.add_argument("--json", action="store_true")

    listing = sub.add_parser("list", help="list chats")
    listing.add_argument("--json", action="store_true")

    export = sub.add_parser("export", help="export one chat (messages + shared media)")
    export.add_argument("--chat", required=True, help="chat title substring or full conversation id")
    export.add_argument("--out", default=str(EXPORT_DIR), help="output folder (default: exports/)")
    export.add_argument("--include-system", action="store_true", help="keep join/leave events")
    export.add_argument("--include-events", action="store_true", help="keep call events")
    export.add_argument("--no-media", action="store_true", help="skip downloading files/images")
    export.add_argument("--zip", action="store_true", help="also write a .zip archive")
    export.add_argument("--json", action="store_true")

    debug = sub.add_parser("discover", help="debug: dump raw API samples, cookies and storage")
    debug.add_argument("--wait-minutes", type=int, default=15)
    debug.add_argument("--url", default="https://teams.live.com/v2/")

    args = parser.parse_args()
    JSON_MODE = bool(getattr(args, "json", False))
    try:
        if args.command == "login":
            result = capture_session(args.timeout, args.keep_open, args.url)
        elif args.command == "status":
            result = command_status()
        elif args.command == "list":
            result = command_list()
        elif args.command == "export":
            result = command_export(args.chat, args.out, args.include_system,
                                    args.include_events, not args.no_media, args.zip)
        elif args.command == "discover":
            result = discover(args.wait_minutes, args.url)
        else:
            result = None
        if JSON_MODE:
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "export" and result:
            print(f"\nExported {result['messages']} messages from {result['chatTitle']!r}")
            if result["skippedEvents"]:
                print(f"  skipped {result['skippedEvents']} join/leave/call events")
            if result["firstMessage"]:
                print(f"  range: {result['firstMessage']} -> {result['lastMessage']}")
            print(f"  participants: {result['participants']}")
            if not args.no_media:
                print(f"  media: {result['mediaDownloaded']} downloaded, {result['mediaFailed']} failed")
            print(f"  folder: {result['folder']}")
            if result["zip"]:
                print(f"  zip: {result['zip']}")
        return 0
    except (RuntimeError, requests.RequestException) as exc:
        if JSON_MODE:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())


