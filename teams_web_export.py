"""Export a Teams (personal) chat by replaying the Teams web API.

Two steps:
1. `python teams_web_export.py discover`: opens a Chromium window with a
   persistent profile, you sign in and open the group chat, scroll a little,
   then create `go.flag` next to this script. API responses and the session
   token are saved under `capture/`.
2. `python teams_web_export.py list` and
   `python teams_web_export.py export --chat "part of the chat name"`
   page through the full history with that token and write JSON + CSV.

Everything stays on this machine.
"""

import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / ".browser_profile"
CAPTURE_DIR = ROOT / "capture"
EXPORT_DIR = ROOT / "exports"
LOG_PATH = CAPTURE_DIR / "run.log"
INTERESTING = re.compile(r"chatsvc|/messages|/conversations|/chats/", re.I)
MAX_BODY = 3_000_000
MAX_ENTRIES = 300

API_ROOT = "https://teams.live.com/api/chatsvc/consumer/v1"


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
VIEW = "msnp24Equivalent|supportsMessageProperties|supportsExtendedHistory"
PAGE_SIZE = 200


def log(message):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    CAPTURE_DIR.mkdir(exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def discover(wait_minutes, start_url):
    CAPTURE_DIR.mkdir(exist_ok=True)
    flag = ROOT / "go.flag"
    if flag.exists():
        flag.unlink()

    captures = []
    seen = set()

    def record_body(entry, response):
        content_type = (response.headers or {}).get("content-type", "")
        if "json" not in content_type.lower():
            entry["body_text"] = response.text()[:4000]
            return
        text = response.text()
        if len(text) > MAX_BODY:
            entry["body_truncated"] = True
            text = text[:MAX_BODY]
        entry["body"] = json.loads(text) if not entry.get("body_truncated") else text

    def on_response(response):
        url = response.url
        if "teams" not in url or not INTERESTING.search(url):
            return
        key = (response.request.method, url)
        if key in seen or len(captures) >= MAX_ENTRIES:
            return
        seen.add(key)
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "method": response.request.method,
            "url": url,
            "status": response.status,
            "request_headers": dict(response.request.headers),
            "post_data": response.request.post_data,
        }
        try:
            record_body(entry, response)
        except Exception as exc:
            entry["body_error"] = str(exc)
        captures.append(entry)
        log(f"captured {entry['method']} {response.status} {url[:160]}")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.on("response", on_response)
        log(f"opening {start_url} - sign in and open the group chat")
        page.goto(start_url, wait_until="domcontentloaded")

        deadline = time.time() + wait_minutes * 60
        started = time.time()
        last_note = 0
        shot_at = {20, 60, 120}
        while time.time() < deadline:
            if flag.exists():
                break
            if page.is_closed():
                log("browser window was closed before go.flag appeared")
                break
            elapsed = int(time.time() - started)
            if elapsed in shot_at:
                shot_at.discard(elapsed)
                try:
                    page.screenshot(path=str(CAPTURE_DIR / f"startup_{elapsed}s.png"))
                    log(f"startup screenshot at {elapsed}s: url={page.url} title={page.title()[:80]}")
                except Exception as exc:
                    log(f"screenshot failed: {exc}")
            if time.time() - last_note > 30:
                log("waiting for go.flag (sign in, open the group chat, scroll up a bit)")
                last_note = time.time()
            time.sleep(1)

        if not flag.exists():
            log("timed out waiting for go.flag - nothing dumped")
            context.close()
            return 1

        page.wait_for_timeout(2000)
        log("go.flag found - dumping state")

        try:
            storage = page.evaluate(
                """() => {
                    const dump = (store) => {
                        const out = {};
                        for (let i = 0; i < store.length; i++) {
                            const k = store.key(i);
                            const v = store.getItem(k) || '';
                            out[k] = v.length < 5000 ? v : v.slice(0, 5000) + '...[truncated]';
                        }
                        return out;
                    };
                    return {localStorage: dump(window.localStorage), sessionStorage: dump(window.sessionStorage)};
                }"""
            )
            (CAPTURE_DIR / "storage.json").write_text(json.dumps(storage, indent=2), encoding="utf-8")
            log(f"storage keys: {len(storage['localStorage'])} local / {len(storage['sessionStorage'])} session")
        except Exception as exc:
            log(f"storage dump failed: {exc}")

        try:
            cookies = context.cookies()
            (CAPTURE_DIR / "cookies.json").write_text(json.dumps(cookies, indent=2), encoding="utf-8")
            log(f"cookies: {len(cookies)}")
        except Exception as exc:
            log(f"cookie dump failed: {exc}")

        try:
            dom = page.evaluate(
                """() => {
                    const tids = {};
                    document.querySelectorAll('[data-tid]').forEach((el) => {
                        const key = el.getAttribute('data-tid');
                        tids[key] = (tids[key] || 0) + 1;
                    });
                    const scrollables = [];
                    document.querySelectorAll('*').forEach((el) => {
                        const style = getComputedStyle(el);
                        if (!/(auto|scroll)/.test(style.overflowY)) return;
                        if (el.scrollHeight - el.clientHeight < 200 || el.clientHeight < 200) return;
                        scrollables.push({
                            tag: el.tagName,
                            tid: el.getAttribute('data-tid'),
                            id: el.id,
                            cls: String(el.className || '').slice(0, 150),
                            scrollHeight: el.scrollHeight,
                            clientHeight: el.clientHeight,
                            text: (el.innerText || '').slice(0, 300),
                        });
                    });
                    scrollables.sort((a, b) => b.clientHeight - a.clientHeight);
                    const biggest = scrollables[0];
                    let html = '';
                    if (biggest) {
                        const target = [...document.querySelectorAll('*')].find((el) => {
                            const style = getComputedStyle(el);
                            return /(auto|scroll)/.test(style.overflowY) && el.scrollHeight - el.clientHeight >= 200 && el.clientHeight >= 200 && el.scrollHeight === biggest.scrollHeight;
                        });
                        if (target) html = target.outerHTML.slice(0, 250000);
                    }
                    return {tids, scrollables: scrollables.slice(0, 25), html};
                }"""
            )
            (CAPTURE_DIR / "dom.json").write_text(json.dumps(dom, indent=2), encoding="utf-8")
            (CAPTURE_DIR / "dom_sample.html").write_text(dom.get("html", ""), encoding="utf-8")
            log(f"dom: {len(dom.get('tids', {}))} data-tid values, {len(dom.get('scrollables', []))} scrollables")
        except Exception as exc:
            log(f"dom dump failed: {exc}")

        (CAPTURE_DIR / "api_capture.json").write_text(json.dumps(captures, indent=2), encoding="utf-8")
        log(f"saved {len(captures)} API captures to capture/api_capture.json")

        try:
            page.screenshot(path=str(CAPTURE_DIR / "final_state.png"))
        except Exception:
            pass

        try:
            context.close()
        except Exception:
            pass
    return 0


def load_token():
    path = CAPTURE_DIR / "api_capture.json"
    if path.exists():
        for entry in json.loads(path.read_text(encoding="utf-8")):
            headers = {k.lower(): v for k, v in (entry.get("request_headers") or {}).items()}
            auth = headers.get("authentication", "")
            if "skypetoken=" in auth:
                return auth.split("skypetoken=", 1)[1].split(";")[0].strip()
    storage_path = CAPTURE_DIR / "storage.json"
    if storage_path.exists():
        storage = json.loads(storage_path.read_text(encoding="utf-8"))
        for store in storage.values():
            for key, value in store.items():
                if "skypetoken" in key.lower() and isinstance(value, str) and value.count(".") == 2:
                    return value
    return None


def api_get(token, url=None, params=None):
    headers = dict(HEADERS_BASE)
    headers["authentication"] = f"skypetoken={token}"
    response = requests.get(url or f"{API_ROOT}/users/ME/conversations", headers=headers,
                            params=params, timeout=60)
    if response.status_code == 401:
        raise RuntimeError(
            "Teams rejected the saved token (401). The token lasts about 8 hours: rerun "
            "`python teams_web_export.py discover`, sign in, open the chat, create go.flag."
        )
    if not response.ok:
        raise RuntimeError(f"Teams API returned HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def rewrite_link(link):
    return (link or "").replace("https://msgapi.teams.live.com/v1", f"{API_ROOT}")


def fetch_conversations(token):
    payload = api_get(token, params={"pageSize": 100, "startTime": 0, "view": VIEW})
    conversations = payload.get("conversations") or []
    seen = {c.get("id") for c in conversations}
    link = rewrite_link((payload.get("_metadata") or {}).get("forwardLink"))
    while link:
        payload = api_get(token, link)
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


def fetch_history(token, conversation_id, max_pages=500):
    quoted = urllib.parse.quote(conversation_id, safe="")
    url = f"{API_ROOT}/users/ME/conversations/{quoted}/messages"
    payload = api_get(token, url, {"pageSize": PAGE_SIZE, "view": VIEW})
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
        print(f"  page {pages}: {len(batch)} messages ({len(messages)} unique so far)", flush=True)
        link = rewrite_link((payload.get("_metadata") or {}).get("backwardLink"))
        if not fresh or not link or pages >= max_pages:
            break
        payload = api_get(token, link)
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
        elif tag == "img":
            self.images.append(attributes.get("alt") or attributes.get("src") or "")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join([line for line in lines if line])


def html_to_text(content):
    if not content:
        return ""
    if "<" not in content:
        return html.unescape(content).strip()
    parser = TextExtractor()
    parser.feed(content)
    parser.close()
    text = parser.text()
    for link in dict.fromkeys(parser.links):
        if link and link not in text:
            text += f"\n{link}"
    for image in dict.fromkeys(parser.images):
        if image:
            text += f"\n[image] {image}"
    return text.strip()


def summarize_call(content):
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return html_to_text(content)[:500]
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


def normalize(message):
    properties = message.get("properties") or {}
    created = message.get("composetime") or message.get("originalarrivaltime") or ""
    timestamp_local = ""
    timestamp_utc = ""
    if created:
        try:
            parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
            timestamp_utc = parsed.astimezone(timezone.utc).isoformat()
            timestamp_local = parsed.astimezone().isoformat(timespec="seconds")
        except ValueError:
            timestamp_utc = created
    files = parse_json_list(properties.get("files"))
    attachments = [f.get("fileName") or f.get("title") or f.get("fileUrl", "") for f in files if isinstance(f, dict)]
    reactions = parse_json_list(properties.get("emotions"))
    body = message.get("content") or ""
    return {
        "timestampUtc": timestamp_utc,
        "timestampLocal": timestamp_local,
        "sender": message.get("imdisplayname") or "",
        "messageType": message.get("messagetype") or "",
        "contentType": message.get("contenttype") or "",
        "text": html_to_text(body),
        "attachments": "; ".join(filter(None, attachments)),
        "reactions": "; ".join(
            f"{r.get('emotion') or r.get('key') or 'reaction'}:{r.get('count') or ''}".rstrip(":")
            for r in reactions if isinstance(r, dict)
        ),
        "messageId": message.get("id") or "",
        "sequenceId": message.get("sequenceId"),
        "threadId": message.get("conversationid") or "",
        "rawContent": body,
    }


def slugify(value, fallback="teams_chat"):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug[:60] or fallback


def write_exports(title, conversation_id, messages, out_dir, include_system, include_events):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = out_dir / f"{slugify(title)}_{stamp}"

    skipped = 0
    exported = []
    for message in messages:
        if not keep_message(message, include_system, include_events):
            skipped += 1
            continue
        item = normalize(message)
        if item["messageType"] == "Event/Call":
            item["text"] = summarize_call(item["rawContent"])
        exported.append(item)
    exported.sort(key=lambda item: (item["timestampUtc"], item["messageId"]))

    payload = {
        "chatTitle": title,
        "conversationId": conversation_id,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "totalMessages": len(exported),
        "messages": exported,
    }
    json_path = base.with_suffix(".json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = base.with_suffix(".csv")
    fieldnames = ["timestampLocal", "timestampUtc", "sender", "messageType", "text",
                  "attachments", "reactions", "messageId", "threadId"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(exported)

    senders = {}
    for item in exported:
        senders[item["sender"] or "(unknown)"] = senders.get(item["sender"] or "(unknown)", 0) + 1
    print(f"\nExported {len(exported)} messages from {title!r}")
    if skipped:
        print(f"  skipped {skipped} join/leave/call events (use --include-system / --include-events)")
    if exported:
        print(f"  first: {exported[0]['timestampLocal']}  last: {exported[-1]['timestampLocal']}")
    print(f"  participants: {len(senders)}")
    print(f"  JSON: {json_path}")
    print(f"  CSV : {csv_path}")
    return json_path, csv_path


def command_list():
    token = load_token()
    if not token:
        print("No saved Teams token. Run `python teams_web_export.py discover` first.")
        return 1
    conversations = fetch_conversations(token)
    conversations.sort(key=lambda c: (c.get("lastMessage") or {}).get("composetime", ""), reverse=True)
    print(f"{len(conversations)} conversations:\n")
    for conversation in conversations:
        last = (conversation.get("lastMessage") or {}).get("composetime", "")[:19]
        print(f"  {conversation_title(conversation) or '(no title)'}  [{last}]")
        print(f"    {conversation.get('id')}")
    return 0


def command_export(chat, out_dir, include_system, include_events):
    token = load_token()
    if not token:
        print("No saved Teams token. Run `python teams_web_export.py discover` first.")
        return 1
    conversations = fetch_conversations(token)
    if chat.startswith("19:"):
        matches = [c for c in conversations if c.get("id") == chat]
    else:
        needle = chat.lower()
        matches = [c for c in conversations if needle in conversation_title(c).lower()]
    if not matches:
        print(f"No conversation matched {chat!r}. Run `python teams_web_export.py list` to see titles.")
        return 1
    if len(matches) > 1:
        print(f"{len(matches)} conversations matched {chat!r}:")
        for conversation in matches:
            print(f"  {conversation_title(conversation)}  {conversation.get('id')}")
        return 1
    conversation = matches[0]
    title = conversation_title(conversation) or "teams_chat"
    print(f"Exporting {title!r} ({conversation['id']})")
    messages = fetch_history(token, conversation["id"])
    write_exports(title, conversation["id"], messages, Path(out_dir), include_system, include_events)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Teams personal chat export via the Teams web API")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="open Teams, capture the session token and API traffic")
    d.add_argument("--wait-minutes", type=int, default=25)
    d.add_argument("--url", default="https://teams.live.com/v2/")

    sub.add_parser("list", help="list chats available to the signed-in account")

    e = sub.add_parser("export", help="export one chat to JSON and CSV")
    e.add_argument("--chat", required=True, help="part of the chat title, or the full conversation id")
    e.add_argument("--out", default=str(EXPORT_DIR), help="output folder (default: exports/)")
    e.add_argument("--include-system", action="store_true",
                   help="keep join/leave events (ThreadActivity messages)")
    e.add_argument("--include-events", action="store_true",
                   help="keep call started/ended events (Event/Call messages)")

    args = parser.parse_args()
    try:
        if args.command == "discover":
            return discover(args.wait_minutes, args.url)
        if args.command == "list":
            return command_list()
        if args.command == "export":
            return command_export(args.chat, args.out, args.include_system, args.include_events)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
