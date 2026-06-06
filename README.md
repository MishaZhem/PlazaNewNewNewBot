# Plaza Bot

Monitors plaza.newnewnew.space for rental listings in Delft and automatically submits applications from your account via the portal API. Sends a Telegram notification for every application (or attempted application).

## Features

- Polls the housing portal every N seconds (configurable)
- Filters listings by city (default: Delft)
- Only processes real housing listings (apartments, studios, rooms) — parking, storage, and vehicle listings are automatically skipped
- Auto-applies to new listings via the portal API
- Persists applied listing ids in `applied.json` — never applies twice
- Telegram notifications for every successful or failed application (includes dwelling type)
- `DRY_RUN` mode for safe testing without submitting real applications
- Graceful degraded mode if login fails (still notifies, skips react)
- Session cookie fallback for accounts that require MFA or when OAuth fails

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Get from [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Yes | Get from [@userinfobot](https://t.me/userinfobot) on Telegram |
| `PLAZA_USERNAME` | Yes | Your plaza.newnewnew.space login (email) |
| `PLAZA_PASSWORD` | Yes | Your plaza.newnewnew.space password |
| `PLAZA_CLIENT_ID` | No | OAuth2 client_id (default: `wzp`). Change only if login fails with `invalid_client` — see below. |
| `PLAZA_SESSION_COOKIE` | Optional | Raw `Cookie` header from a logged-in browser session. Use as fallback if OAuth fails (e.g. MFA accounts). |
| `TARGET_CITY` | No | City to watch (default: `Delft`) |
| `POLL_INTERVAL_SECONDS` | No | How often to poll in seconds (default: `60`) |
| `DRY_RUN` | No | Set to `true` to notify without submitting applications (default: `false`) |

### Authentication

**Primary (recommended):** set `PLAZA_USERNAME` and `PLAZA_PASSWORD`. The bot uses an OAuth2 password grant against `auth.hexia.io`, then exchanges the token for a plaza session cookie automatically.

If login fails with `invalid_client`, the `client_id` value used by the site may have changed. Capture the real value from browser DevTools: Network → request to `auth.hexia.io/api/v1/oauth/token` → request payload → `client_id`, then set `PLAZA_CLIENT_ID` in `.env`.

**Fallback:** if your account requires MFA or OAuth login does not work, copy the `Cookie` header from a logged-in browser session (DevTools → Network → any plaza request → `Cookie` header) into `PLAZA_SESSION_COOKIE`.

### Getting your Telegram token and chat id

1. **Bot token**: message [@BotFather](https://t.me/BotFather), send `/newbot`, follow prompts.
2. **Chat id**: message [@userinfobot](https://t.me/userinfobot) — it replies with your chat id.

## Run

```bash
python bot.py
```

## DRY_RUN mode

Set `DRY_RUN=true` in `.env` to test the full pipeline safely. The bot will poll, filter, and send Telegram notifications marked `[DRY_RUN]`, but will **not** submit any applications.

## Notes

- `applied.json` is created automatically and tracks all submitted application ids.
- `login()` and `react()` require valid credentials; they are **not** tested during build — validate with your real account.
- The bot runs indefinitely; use `Ctrl+C` or a process manager (systemd, screen, etc.) to stop it.
