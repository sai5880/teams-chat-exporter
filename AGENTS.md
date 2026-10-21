# AGENTS.md â€” instructions for AI agents

You are operating **Teams Chat Exporter**, a local CLI + MCP tool that exports a user's **personal / Teams Free** chats (messages, shared files, images, archives) to JSON and CSV.

Use this tool when the user asks to export, back up, archive, read, or analyze their own Teams chats and shared media. Do **not** use Microsoft Graph for personal accounts: Graph does not support delegated personal-account access to `/chats/{id}/messages`.

## Hard rules

1. **Never print, log, commit, or transmit** the contents of `capture/session.json` (session token), `capture/cookies.json`, or `.browser_profile/`. Treat them as passwords.
2. **Never commit or publish** `capture/`, `exports/`, `.browser_profile/`, or `.env`. They are gitignored; keep it that way.
3. **Never automate the Microsoft sign-in.** `login` opens a browser for a human. If a session is missing or expired, stop and ask the user to run:
   `python teams_web_export.py login`
4. **Never bypass the allowlist.** Media is downloaded only from Microsoft hosts (`teams.live.com`, `*.skype.com`, `*.sharepoint.com`, `*.onedrive.com`, `*.live.com`, `*.office.com`, ...). Do not modify this to fetch arbitrary URLs.
5. Export only the account owner's own chats. Do not attempt to access other users' data.

## Environment check

Run from the repository root. Verify Python and dependencies:

```powershell
.\.venv\Scripts\python.exe teams_web_export.py status --json
```

Expected when ready: `{"ok": true, "session": true, "expired": false, ...}`.

If `session` is false or `expired` is true, ask the human to run `login` in a normal terminal, then continue.

## Commands (machine-readable)

All commands accept `--json` and print exactly one JSON object on stdout. Progress logs go to stderr. Exit code `0` = success, `1` = handled error (`{"ok": false, "error": "..."}`), `2` = invalid usage.

### List chats

```powershell
.\.venv\Scripts\python.exe teams_web_export.py list --json
```

```json
{"ok": true, "count": 2, "chats": [{"title": "Project Alpha", "id": "19:...@thread.v2", "lastMessage": "2025-01-01T10:00:00Z", "members": 12}]}
```

Pick the chat by **id**, not title, when several titles are similar.

### Export a chat with media

```powershell
.\.venv\Scripts\python.exe teams_web_export.py export --chat "19:...@thread.v2" --zip --json
```

Options: `--chat` (title substring or full id, required), `--out DIR`, `--no-media`, `--zip`, `--include-system`, `--include-events`.

```json
{
  "ok": true,
  "chatTitle": "Project Alpha",
  "conversationId": "19:...@thread.v2",
  "messages": 209,
  "skippedEvents": 563,
  "participants": 97,
  "mediaDownloaded": 14,
  "mediaSkipped": 3,
  "mediaFailed": 2,
  "firstMessage": "2024-05-21T11:31:45+05:30",
  "lastMessage": "2024-06-03T09:49:11+05:30",
  "folder": "exports/project_alpha_20250101T120000Z",
  "json": "exports/project_alpha_20250101T120000Z/messages.json",
  "csv": "exports/project_alpha_20250101T120000Z/messages.csv",
  "manifest": "exports/project_alpha_20250101T120000Z/manifest.json",
  "zip": "exports/project_alpha_20250101T120000Z.zip"
}
```

`mediaFailed > 0` is not fatal: inspect `manifest.json` for `status`/`error` per item. `manifest.json` statuses: `downloaded`, `reused` (same attachment seen earlier), `skipped` (emoji/sticker CDN assets), `failed` (see `error`).

## Output layout

```
exports/<chat>_<UTC stamp>/
  messages.json    all messages, full schema incl. rawContent and media[]
  messages.csv     UTF-8 BOM, spreadsheet-friendly
  manifest.json    every media item with source URL, local path, bytes, status, error
  media/           downloaded files and images (original names where available)
exports/<chat>_<UTC stamp>.zip   (only with --zip)
```

Key message fields: `timestampUtc`, `timestampLocal`, `sender`, `messageType`, `text`, `attachments`, `mediaFiles`, `reactions`, `messageId`, `threadId`.

## Typical agent workflow

1. `status --json` â†’ if no valid session, ask the human to run `login`.
2. `list --json` â†’ locate the target chat id.
3. `export --chat "<id>" --zip --json` â†’ report paths and counts to the user.
4. If the user wants analysis, read `messages.csv` / `messages.json` from the returned paths. Do not paste private chat content into public places.

## MCP server (preferred for tool-using agents)

`mcp_server.py` speaks MCP over stdio and exposes `teams_status`, `teams_list_chats`, `teams_export_chat`.

- Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "teams-chat-exporter": {
      "command": "C:\\path\\to\\repo\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\repo\\mcp_server.py"]
    }
  }
}
```

- opencode (`opencode.json`):

```json
{
  "mcp": {
    "teams-chat-exporter": {
      "type": "local",
      "command": ["C:\\path\\to\\repo\\.venv\\Scripts\\python.exe", "C:\\path\\to\\repo\\mcp_server.py"],
      "enabled": true
    }
  }
}
```

- Generic stdio client: spawn the interpreter with `mcp_server.py`, send newline-delimited JSON-RPC (`initialize`, `tools/list`, `tools/call`).

MCP tool calls return a single text content block containing the same JSON as the CLI `--json` output.

## Error handling

| Error text | Meaning | Action |
| --- | --- | --- |
| `No saved session` / `401` | Session missing or expired (~8h lifetime) | Ask the human to run `login` |
| `No conversation matched` | Bad `--chat` value | Run `list --json`, use the exact id |
| `Multiple chats matched` | Ambiguous title | Re-run with the full `19:...` id |
| `got an HTML sign-in page` (in manifest) | File host needs a fresh interactive session | Rerun `login`; file URL is still stored |
| `blocked host` (in manifest) | Media URL outside Microsoft allowlist | Leave it; report to user |

## Answering common user questions

- "Do I have to open the chat and scroll?" **No.** `login` only needs the chat list to load. History is fetched through the API with backward pagination, and media is downloaded directly.
- "Does it export files, images, ZIPs?" **Yes**, when `--media` is on (default). Failures are listed in `manifest.json`.
- "All chats or one?" **One chat per `export` call.** Call `export` for each chat id from `list`.

