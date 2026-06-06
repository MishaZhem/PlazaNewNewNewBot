"""
Plaza bot — polls plaza.newnewnew.space for Delft housing listings and
automatically submits applications from the configured account.
"""

import logging
import time

from config import Config
from notifier import Notifier
from plaza_client import PlazaClient, filter_city, is_housing
from storage import load_applied, save_applied

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("plaza_bot")


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
        "Starting poll loop — city=%s interval=%ds degraded=%s",
        cfg.TARGET_CITY,
        cfg.POLL_INTERVAL_SECONDS,
        degraded,
    )

    while True:
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

        except Exception as e:
            logger.exception("Unhandled error in poll loop: %s", e)

        time.sleep(cfg.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
