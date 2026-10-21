# Security

## Reporting

Open a private security advisory on GitHub or contact the maintainer through the repository. Please do not file public issues for token or data leaks.

## Sensitive files

These paths are gitignored and must never be committed:

| Path | Why |
| --- | --- |
| `capture/` | Live Teams session token, cookies, storage contents, API samples |
| `.browser_profile/` | Signed-in Chromium profile |
| `exports/` | Private chat content |
| `.env` | Local app secrets |

Delete `capture/` and `exports/` when you are done exporting, and sign out of the browser profile if you shared the machine.

## Scope

This tool runs locally, reads only the signed-in user's own chats, and sends data nowhere except `teams.live.com`. It does not bypass authentication or access other users' data.
