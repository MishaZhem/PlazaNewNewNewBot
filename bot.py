"""
Plaza bot — polls plaza.newnewnew.space for Delft housing listings and
automatically submits applications from the configured account.
"""

import logging
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Config
from notifier import Notifier
from plaza_client import PlazaClient, RateLimited, filter_city, is_housing
from storage import load_applied, save_applied

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("plaza_bot")

_AMS = ZoneInfo("Europe/Amsterdam")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _address(item: dict) -> str:
    """Build a human-readable address string from a listing dict."""
    parts = [
        item.get("street", ""),
        item.get("houseNumber", ""),
        item.get("houseNumberAddition", ""),
    ]
    address = " ".join(p for p in parts if p).strip()
    city_name = item.get("city", {}).get("name", "")
    if city_name:
        address = f"{address}, {city_name}"
    return address


def _detail_url(item: dict) -> str:
    url_key = item.get("urlKey", "")
    return f"https://plaza.newnewnew.space/aanbod/huurwoningen/details/{url_key}"


def _looks_like_session_expired(text: str) -> bool:
    indicators = ("not authenticated", "not-authenticated", "login", "401", "session")
    low = text.lower()
    return any(ind in low for ind in indicators)


def _in_active_hours(cfg) -> bool:
    """Return True if the current Amsterdam time is within the configured active window.

    The window is ``[ACTIVE_HOURS_START, ACTIVE_HOURS_END)``.
    ACTIVE_HOURS_END == 24 means the window extends until midnight.
    """
    hour = datetime.now(_AMS).hour
    return cfg.ACTIVE_HOURS_START <= hour < cfg.ACTIVE_HOURS_END


def _seconds_until_active(cfg) -> int:
    """Compute seconds until the next ACTIVE_HOURS_START in Amsterdam time.

    Always returns a positive value (minimum 60 s) so callers can sleep safely.
    """
    now = datetime.now(_AMS)
    hour = now.hour
    minute = now.minute
    second = now.second
    start = cfg.ACTIVE_HOURS_START

    minutes_elapsed_in_hour = minute * 60 + second
    if hour < start:
        # Still today — wait from now until start hour
        seconds = (start - hour) * 3600 - minutes_elapsed_in_hour
    else:
        # start is tomorrow
        seconds = (24 - hour + start) * 3600 - minutes_elapsed_in_hour

    return max(60, int(seconds))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = Config
    client = PlazaClient()
    notifier = Notifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)

    applied = load_applied()
    logged_in = False
    degraded = False

    if cfg.DRY_RUN:
        logger.info("DRY_RUN mode enabled — no real applications will be submitted")

    # Login
    logged_in = client.login(
        cfg.PLAZA_USERNAME,
        cfg.PLAZA_PASSWORD,
        session_cookie=cfg.PLAZA_SESSION_COOKIE or None,
        client_id=cfg.PLAZA_CLIENT_ID,
        session_file=cfg.SESSION_FILE,
    )
    if not logged_in:
        if cfg.DRY_RUN:
            logger.warning(
                "Login failed but DRY_RUN is enabled — continuing in dry-run mode"
            )
        else:
            logger.error(
                "Login failed — check PLAZA_USERNAME/PLAZA_PASSWORD (and PLAZA_CLIENT_ID), "
                "or set PLAZA_SESSION_COOKIE as fallback. Running in degraded mode: "
                "listings will be fetched and notifications sent, but react() is skipped."
            )
            degraded = True

    # Sync applied set with server state
    if logged_in:
        try:
            server_ids = client.get_active_reactions()
            if server_ids:
                before = len(applied)
                applied |= server_ids
                save_applied(applied)
                logger.info(
                    "Synced applied set: %d local + %d from server = %d total",
                    before,
                    len(server_ids),
                    len(applied),
                )
        except Exception as e:
            logger.warning("Could not sync active reactions at startup: %s", e)

    logger.info(
        "Starting poll loop — city=%s poll=[%ds,%ds] active_hours=[%d,%d) degraded=%s",
        cfg.TARGET_CITY,
        cfg.POLL_INTERVAL_MIN_SECONDS,
        cfg.POLL_INTERVAL_MAX_SECONDS,
        cfg.ACTIVE_HOURS_START,
        cfg.ACTIVE_HOURS_END,
        degraded,
    )

    _sleeping_outside_hours = False  # track transitions to avoid log spam

    while True:
        # -- Active-hours gate --
        if not _in_active_hours(cfg):
            if not _sleeping_outside_hours:
                secs = _seconds_until_active(cfg)
                logger.info(
                    "Outside active hours (%d–%d Amsterdam). "
                    "Next check in ~%ds. Polls paused.",
                    cfg.ACTIVE_HOURS_START,
                    cfg.ACTIVE_HOURS_END,
                    secs,
                )
                _sleeping_outside_hours = True
            nap = min(900, _seconds_until_active(cfg))
            time.sleep(nap)
            continue

        # Transitioned back into active hours
        if _sleeping_outside_hours:
            logger.info("Re-entered active hours — resuming polls.")
            _sleeping_outside_hours = False

        try:
            listings = client.get_listings()
            delft = filter_city(listings, cfg.TARGET_CITY)
            housing = [it for it in delft if is_housing(it)]
            skipped = len(delft) - len(housing)
            logger.info(
                "Polled: %d total listings, %d in %s, %d housing, %d skipped (non-housing)",
                len(listings),
                len(delft),
                cfg.TARGET_CITY,
                len(housing),
                skipped,
            )

            for item in housing:
                obj_id = str(item.get("id", ""))
                if not obj_id or obj_id in applied:
                    continue

                address = _address(item)
                rent = item.get("netRent", "?")
                url = _detail_url(item)
                closing = item.get("closingDate", "?")
                dwelling_type = item.get("dwellingType", {}).get("localizedName", "?")

                logger.info(
                    "New listing found: %s (id=%s, type=%s)",
                    address,
                    obj_id,
                    dwelling_type,
                )

                if cfg.DRY_RUN:
                    delay = random.uniform(
                        cfg.APPLY_DELAY_MIN_SECONDS, cfg.APPLY_DELAY_MAX_SECONDS
                    )
                    logger.info(
                        "[DRY_RUN] would wait ~%.0fs before applying to %s",
                        delay,
                        address,
                    )
                    msg = (
                        f"[DRY_RUN] Would apply to:\n"
                        f"  {address}\n"
                        f"  Type: {dwelling_type}\n"
                        f"  Rent: €{rent}\n"
                        f"  Closes: {closing}\n"
                        f"  {url}"
                    )
                    notifier.send(msg)
                    logger.info("DRY_RUN: skipped react() for id=%s", obj_id)
                    applied.add(obj_id)
                    save_applied(applied)
                    continue

                if degraded:
                    msg = (
                        f"[DEGRADED — NOT applied, login failed]\n"
                        f"  {address}\n"
                        f"  Type: {dwelling_type}\n"
                        f"  Rent: €{rent}\n"
                        f"  Closes: {closing}\n"
                        f"  {url}"
                    )
                    notifier.send(msg)
                    logger.warning(
                        "Degraded mode: skipping react() for id=%s. "
                        "Fix login to enable auto-apply.",
                        obj_id,
                    )
                    continue

                # Human delay before applying
                apply_delay = random.uniform(
                    cfg.APPLY_DELAY_MIN_SECONDS, cfg.APPLY_DELAY_MAX_SECONDS
                )
                logger.info(
                    "Waiting %.0fs before applying to %s to look human",
                    apply_delay,
                    address,
                )
                time.sleep(apply_delay)

                # Attempt to apply
                ok, snippet = client.react(obj_id)

                # One re-login retry if session appears expired
                if not ok and _looks_like_session_expired(snippet):
                    logger.warning(
                        "Session may have expired for id=%s, attempting re-login", obj_id
                    )
                    re_logged = client.login(
                        cfg.PLAZA_USERNAME,
                        cfg.PLAZA_PASSWORD,
                        session_cookie=cfg.PLAZA_SESSION_COOKIE or None,
                        client_id=cfg.PLAZA_CLIENT_ID,
                        session_file=cfg.SESSION_FILE,
                    )
                    if re_logged:
                        ok, snippet = client.react(obj_id)

                if ok:
                    applied.add(obj_id)
                    save_applied(applied)
                    msg = (
                        f"Applied!\n"
                        f"  {address}\n"
                        f"  Type: {dwelling_type}\n"
                        f"  Rent: €{rent}\n"
                        f"  Closes: {closing}\n"
                        f"  {url}"
                    )
                    notifier.send(msg)
                    logger.info("Applied to id=%s (%s)", obj_id, address)
                else:
                    msg = (
                        f"Failed to apply:\n"
                        f"  {address}\n"
                        f"  Type: {dwelling_type}\n"
                        f"  Rent: €{rent}\n"
                        f"  {url}\n"
                        f"  Reason: {snippet[:200]}"
                    )
                    notifier.send(msg)
                    logger.error(
                        "react() failed for id=%s: %s", obj_id, snippet[:200]
                    )

        except RateLimited as e:
            cooldown = cfg.RATE_LIMIT_COOLDOWN_SECONDS
            minutes = cooldown // 60
            logger.warning(
                "Rate-limited (HTTP %s) — cooling down for %d minutes to avoid a ban",
                e,
                minutes,
            )
            notifier.send(
                f"[WARNING] Plaza rate-limited us (HTTP {e}). "
                f"Pausing for {minutes} min to avoid a ban."
            )
            time.sleep(cooldown)
            continue

        except Exception as e:
            logger.exception("Unhandled error in poll loop: %s", e)

        sleep_secs = random.uniform(
            cfg.POLL_INTERVAL_MIN_SECONDS, cfg.POLL_INTERVAL_MAX_SECONDS
        )
        logger.debug("Sleeping %.1fs until next poll", sleep_secs)
        time.sleep(sleep_secs)


if __name__ == "__main__":
    main()
