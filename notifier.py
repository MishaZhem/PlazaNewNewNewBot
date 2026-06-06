"""Telegram notification helper.

If TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are configured, messages are sent
via the Telegram Bot API. Otherwise they are printed to stdout.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    """Send text notifications via Telegram or fall back to stdout."""

    def __init__(self, token: Optional[str], chat_id: Optional[str]) -> None:
        self._token = token or ""
        self._chat_id = chat_id or ""

    def send(self, text: str) -> None:
        """Send *text* as a Telegram message (or print if not configured).

        Never raises — any error is logged as a warning.
        """
        if not self._token or not self._chat_id:
            print(text)
            return

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": False,
        }
        try:
            resp = httpx.post(url, json=payload, timeout=10)
            if resp.status_code >= 400:
                logger.warning(
                    "Telegram sendMessage returned %d: %.200s",
                    resp.status_code,
                    resp.text,
                )
        except Exception as e:
            logger.warning("Telegram notification failed: %s", e)
