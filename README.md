# Plaza Bot

Monitors plaza.newnewnew.space for rental listings in Delft and automatically submits applications from your account via the portal API. Sends a Telegram notification for every application (or attempted application).

> **Disclaimer:** Auto-applying may violate the site's terms of service and a ban is still possible — these measures only reduce risk.

## Features

- Polls the housing portal on a randomized interval (configurable range, default 45–90 s)
- Filters listings by city (default: Delft)
- Only processes real housing listings (apartments, studios, rooms) — parking, storage, and vehicle listings are automatically skipped
- Auto-applies to new listings via the portal API
- Persists applied listing ids in `applied.json` — never applies twice
- Telegram notifications for every successful or failed application (includes dwelling type)
- `DRY_RUN` mode for safe testing without submitting real applications
- Graceful degraded mode if login fails (still notifies, skips react)
- Session cookie fallback for accounts that require MFA or when OAuth fails

## Anti-detection / safe pacing

The bot includes several measures to reduce the risk of detection or a ban:

- **Randomized poll interval** — each sleep between polls is drawn uniformly from `[POLL_INTERVAL_MIN_SECONDS, POLL_INTERVAL_MAX_SECONDS]` (default 45–90 s) rather than a fixed value.
- **Active-hours window** — polls and applications run 24/7 by default (0–24, full day) in the Europe/Amsterdam timezone. Can optionally be narrowed to a specific time range via `ACTIVE_HOURS_START` and `ACTIVE_HOURS_END`. Outside the configured window the bot sleeps and re-checks every 15 minutes.
- **Human delay before applying** — before each real application the bot waits a random interval drawn from `[APPLY_DELAY_MIN_SECONDS, APPLY_DELAY_MAX_SECONDS]` (default 10–60 s). In `DRY_RUN` mode no actual sleep occurs.
- **Consistent browser fingerprint** — every request carries a realistic Chrome/macOS `User-Agent` and matching `sec-ch-ua` / `Sec-Fetch-*` headers identical to what a real browser sends.
- **Session reuse** — after a successful login the bot persists cookies and tokens to `session.json` and reuses them on the next start (trying a refresh-token grant before falling back to a full password login), resulting in fewer logins.
- **Rate-limit back-off** — if the server returns HTTP 429 or 403 the bot logs a warning, sends a Telegram alert, and pauses for `RATE_LIMIT_COOLDOWN_SECONDS` (default 1800 s / 30 min) before resuming.

Relevant env vars: `POLL_INTERVAL_MIN_SECONDS`, `POLL_INTERVAL_MAX_SECONDS`, `ACTIVE_HOURS_START`, `ACTIVE_HOURS_END`, `APPLY_DELAY_MIN_SECONDS`, `APPLY_DELAY_MAX_SECONDS`, `RATE_LIMIT_COOLDOWN_SECONDS`, `SESSION_FILE`.

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
| `POLL_INTERVAL_MIN_SECONDS` | No | Minimum poll interval in seconds (default: `45`) |
| `POLL_INTERVAL_MAX_SECONDS` | No | Maximum poll interval in seconds (default: `90`) |
| `ACTIVE_HOURS_START` | No | Start of active window, Amsterdam time, 0–23 (default: `0`) |
| `ACTIVE_HOURS_END` | No | End of active window, Amsterdam time, 1–24 (default: `24`); set to `0` and `24` for 24/7 |
| `APPLY_DELAY_MIN_SECONDS` | No | Minimum pre-apply human delay in seconds (default: `10`) |
| `APPLY_DELAY_MAX_SECONDS` | No | Maximum pre-apply human delay in seconds (default: `60`) |
| `RATE_LIMIT_COOLDOWN_SECONDS` | No | Pause after a 429/403 response in seconds (default: `1800`) |
| `SESSION_FILE` | No | Path to session persistence file (default: `session.json` next to `bot.py`) |
| `DRY_RUN` | No | Set to `true` to notify without submitting applications (default: `false`) |

### Authentication

**Primary (recommended):** set `PLAZA_USERNAME` and `PLAZA_PASSWORD`. The bot uses an OAuth2 password grant against `auth.hexia.io`, then exchanges the token for a plaza session cookie automatically. On success the session is saved to `session.json` and reused on subsequent starts.

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

Set `DRY_RUN=true` in `.env` to test the full pipeline safely. The bot will poll, filter, and send Telegram notifications marked `[DRY_RUN]`, but will **not** submit any applications or sleep the pre-apply delay.

## Notes

- `applied.json` is created automatically and tracks all submitted application ids.
- `session.json` is created automatically after the first successful login and is excluded from git.
- `login()` and `react()` require valid credentials; they are **not** tested during build — validate with your real account.
- The bot runs indefinitely; use `Ctrl+C` or a process manager (systemd, screen, etc.) to stop it.
