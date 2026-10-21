# Teams Chat Exporter (personal accounts)

Export your own **Microsoft Teams Free / personal** chats and group chats to JSON and CSV — the thing Microsoft Graph refuses to do.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-windows%20%7C%20macos%20%7C%20linux-lightgrey)]()

> Graph's `/chats/{id}/messages` endpoint is marked **"Delegated (personal Microsoft account): Not supported"** in Microsoft's docs. A registered Entra app cannot fix that. This tool works around it by reusing your own signed-in Teams web session locally — no app registration, no admin consent, no third-party server.

## Features

- Works with **personal Microsoft accounts** (Teams Free), including group chats and meeting chats.
- **Full history**: follows the API's `backwardLink` pagination instead of stopping at the newest page.
- Clean output: sender, local + UTC timestamp, plain text (HTML stripped), links, attachments, reactions, message id, thread id.
- JSON + Excel-friendly CSV (UTF-8 BOM).
- Optional join/leave and call events (`--include-system`, `--include-events`).
- Everything runs **locally**: no telemetry, no uploads, no cloud service.

## How it works

1. `discover` opens a Chromium window with a dedicated persistent profile. You sign in to Teams normally; the script records the session token and API samples into a local `capture/` folder.
2. `export` replays the Teams web client's own endpoint (`/api/chatsvc/consumer/v1/users/ME/conversations/{id}/messages`) with that token, paginating backwards until the start of the thread.

The token is a normal Teams session token (about 8 hours). Nothing is stored anywhere except your machine.

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

### 1. Capture a session

```powershell
python teams_web_export.py discover
```

- A browser window opens at Teams.
- Sign in and open the chat you want.
- Scroll the history a little so Teams loads messages.
- Create an empty file named `go.flag` in the project folder (any way you like) to finish the capture.

### 2. List chats and export

```powershell
python teams_web_export.py list
python teams_web_export.py export --chat "part of the chat name"
```

You can also pass the full conversation id (`--chat 19:...@thread.v2`). Output lands in `exports/`:

```
exports/
  my_group_chat_20250101T120000Z.json
  my_group_chat_20250101T120000Z.csv
```

Flags:

| Flag | Effect |
| --- | --- |
| `--chat` | Chat title substring or full conversation id (required) |
| `--out` | Output folder (default `exports/`) |
| `--include-system` | Keep join/leave/rename events |
| `--include-events` | Keep call started/ended events (summarized) |

## Output format

JSON:

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
      "attachments": "",
      "reactions": "like:2",
      "messageId": "1730538843000",
      "sequenceId": 42,
      "threadId": "19:...@thread.v2",
      "rawContent": "<p>Deploy is done</p>"
    }
  ]
}
```

CSV columns: `timestampLocal, timestampUtc, sender, messageType, text, attachments, reactions, messageId, threadId`.

## Privacy and security

- `capture/` contains a **live session token** and cookies. Treat it like a password, never commit or share it, and delete it when finished (`capture/` is gitignored).
- The tool only talks to `teams.live.com`; it never sends your data anywhere else.
- Exports contain private conversations. `exports/` and `.browser_profile/` are gitignored.
- Sessions expire after roughly 8 hours; rerun `discover` when `export` reports a 401.

## Limitations

- Personal accounts only. Work/school accounts should use Graph with admin-approved `Chat.Read` (a Flask sample is included in `app.py`).
- Undocumented endpoint: Microsoft may change it without notice. The `discover` mode exists precisely so selectors/endpoints can be re-checked.
- Attachments are listed, not downloaded; inline images appear as `[image]` placeholders.
- One chat per run; no PDF output (CSV/JSON can be imported elsewhere for that).

## Troubleshooting

- **Stuck on the Teams splash screen**: close the window and rerun `discover`; the saved profile keeps you signed in.
- **`No conversation matched`**: run `list` and copy the exact title fragment or id.
- **401 from the Teams API**: the token expired — rerun `discover`.
- **Empty chat list**: make sure you signed in with the personal account that owns the chats, not a work/school one.

## License

MIT — see [LICENSE](LICENSE).
