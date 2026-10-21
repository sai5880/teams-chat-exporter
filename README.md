# Teams Chat Exporter (personal accounts)

Export your own **Microsoft Teams Free / personal** chats and group chats — messages, shared files, images and ZIPs — to JSON, CSV and a ZIP archive.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-ready-purple)](AGENTS.md)

> Microsoft Graph's `/chats/{id}/messages` endpoint is marked **"Delegated (personal Microsoft account): Not supported"** in Microsoft's docs, and no app registration changes that. This tool instead reuses your own signed-in Teams web session locally: no Entra app, no admin consent, no third-party server.

## Features

- Works with **personal Microsoft accounts** (Teams Free), including group and meeting chats.
- **One sign-in, no scrolling.** After `login`, chats are listed and full history is paged through the API — you do not open or scroll any chat.
- **Media export**: shared files, attachments, ZIPs and inline images are downloaded into `media/`, with a `manifest.json` recording source URL, size and status (`downloaded`, `reused`, `skipped` for emoji/sticker CDN assets, `failed`).
- **Built-in reader**: every export compiles an offline Teams-style `chat.html` with avatars, day separators, image lightbox, attachment cards, dark mode and print support.
- **Search everything**: results sidebar over every message in the JSON (not just rendered ones) with click-to-jump and match highlighting, plus **Load all messages** alongside incremental loading.
- Clean messages: sender, local + UTC timestamp, plain text, links, reactions, message id, thread id.
- JSON + Excel-friendly CSV (UTF-8 BOM) + optional `.zip` of the whole export.
- Optional join/leave and call events (`--include-system`, `--include-events`).
- **Agent-ready**: `--json` on every command, exit codes, [AGENTS.md](AGENTS.md) contract, and a dependency-free [MCP server](mcp_server.py).
- Fully local: no telemetry, no uploads.

## How it works

1. `login` opens Chromium with a dedicated profile. Once you sign in to Teams, the script saves the session token locally and closes the browser. It does **not** read messages from the page.
2. `list` / `export` replay the Teams web client's own API (`/api/chatsvc/consumer/v1/...`) with that token, paginating backwards until the thread start, then download declared files and images.

The token is a normal Teams session token, valid for about 8 hours.

## Quickstart

Requires Python 3.10+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

### 1. Sign in once

```powershell
python teams_web_export.py login
```

A browser opens. Sign in with the personal account that owns the chats. The window closes by itself as soon as Teams loads — no chat needs to be opened or scrolled.

### 2. List chats

```powershell
python teams_web_export.py list
```

### 3. Export

```powershell
python teams_web_export.py export --chat "part of the chat name" --zip
```

Pass a full conversation id (`19:...@thread.v2`) when titles are ambiguous. Output:

```
exports/
  my_group_chat_20250101T120000Z/
    messages.json     all messages (+ rawContent, media[])
    messages.csv      timestampLocal, sender, text, attachments, mediaFiles, ...
    manifest.json     every media item: source, local path, bytes, status, error
    chat.html         offline Teams-style reader (open in any browser)
    media/            downloaded files, images, ZIPs (original names where possible)
  my_group_chat_20250101T120000Z.zip
```

Flags:

| Flag | Effect |
| --- | --- |
| `--chat` | Chat title substring or full conversation id (required) |
| `--out` | Output folder (default `exports/`) |
| `--no-media` | Skip downloading files/images |
| `--zip` | Also write a `.zip` of the export folder (includes `chat.html`) |
| `--no-viewer` | Skip building `chat.html` |
| `--include-system` | Keep join/leave/rename events |
| `--include-events` | Keep call events (summarized) |

Rebuild the reader for an existing export, or make a portable single file:

```powershell
python teams_web_export.py view exports\my_group_chat_20250101T120000Z --open
python teams_web_export.py view exports\my_group_chat_20250101T120000Z --embed --open
```

`--embed` inlines images into `chat.html` so the single file can be shared on its own (larger, no `media/` needed).

## Using it from AI agents

- Read [AGENTS.md](AGENTS.md) — it documents the command contract, JSON schemas, error table and hard security rules.
- Every command supports `--json` for a single parseable object on stdout.
- `mcp_server.py` exposes `teams_status`, `teams_list_chats`, `teams_export_chat` and `teams_build_viewer` over MCP stdio. Example client config is in AGENTS.md.

## Output format

```json
{
  "chatTitle": "Project Alpha",
  "conversationId": "19:...@thread.v2",
  "exportedAt": "2025-01-01T12:00:00+00:00",
  "totalMessages": 2,
  "messages": [
    {
      "timestampUtc": "2024-11-02T09:14:03+00:00",
      "timestampLocal": "2024-11-02T14:44:03+05:30",
      "sender": "Alex",
      "messageType": "RichText/Html",
      "contentType": "Text",
      "text": "Deploy is done",
      "attachments": "logs.zip",
      "mediaFiles": "media/20241102_0_logs.zip",
      "reactions": "like:2",
      "messageId": "1730538843000",
      "sequenceId": 42,
      "threadId": "19:...@thread.v2",
      "media": [
        {"kind": "file", "name": "logs.zip", "source": "https://...", "status": "downloaded", "path": "media/20241102_0_logs.zip", "bytes": 5120, "error": ""}
      ],
      "rawContent": "<p>Deploy is done</p>"
    }
  ]
}
```

## Privacy and security

- `capture/session.json` holds a **live session token** and cookies. Treat it like a password; it is gitignored and should be deleted when finished.
- `exports/` contains private conversations and files. Also gitignored.
- Media downloads are restricted to Microsoft hosts; redirects are re-checked against that allowlist.
- No data leaves your machine except requests to `teams.live.com` and, when downloading attachments, the Microsoft host serving the file.

## Limitations

- Personal accounts only. Work/school accounts should use Graph with admin-approved `Chat.Read` (a Flask sample is included in `app.py`).
- Undocumented endpoint: Microsoft can change it. `discover` exists to re-check raw traffic when that happens.
- Attachments are listed and downloaded when reachable; some file links may require a fresh interactive session and will be reported in `manifest.json` instead of downloaded.
- One chat per `export` call.

## FAQ

**Do I need to open a chat and scroll through it?**
No. `login` only needs the chat list to load. History and media come from the API, so exports work even for chats you never open.

**Does it export everything shared in a group, not just text?**
Yes: files, images, ZIPs and other attachments are downloaded with `--media` (on by default) and recorded in `manifest.json`.

**Why not use Microsoft Graph?**
Graph chat messages do not support delegated personal accounts at all. Registering an Entra app cannot fix it.

**Token stopped working?**
Sessions last roughly 8 hours. Run `login` again.

## License

MIT — see [LICENSE](LICENSE).
