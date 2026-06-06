"""
Client for the plaza.newnewnew.space housing portal API.

Authentication flow
-------------------
Primary path — OAuth2 password grant:
  1. POST https://auth.hexia.io/api/v1/oauth/token with JSON body
     {client_id, grant_type="password", username, password}.
  2. Extract the access token from the response and set the Authorization header.
  3. POST /portal/account/frontend/loginbyservice/format/json (relative to
     plaza base_url) — converts the OAuth token into a server-side session cookie.
  4. Verify with is_logged_in().

Cookie fallback — if PLAZA_SESSION_COOKIE is set, inject it directly and verify.

is_logged_in() and react() work once either path succeeds (Bearer header + session
cookie are both carried on subsequent requests by the shared httpx.Client).
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://plaza.newnewnew.space"

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


def filter_city(listings: list[dict], city: str) -> list[dict]:
    """Return only listings whose city.name matches *city* (case-insensitive)."""
    target = city.strip().lower()
    return [
        item for item in listings
        if item.get("city", {}).get("name", "").lower() == target
    ]


def is_housing(item: dict) -> bool:
    """True if the listing is actual housing (not parking/storage/vehicle)."""
    cat = (item.get("dwellingType") or {}).get("categorie", "")
    return cat == "woning"


class PlazaClient:
    """Thin HTTP wrapper around the plaza.newnewnew.space portal API."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers=_DEFAULT_HEADERS,
            timeout=30,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_listings(self) -> list[dict]:
        """Fetch all listings (no auth required).

        Returns the list under the ``result`` key, or an empty list on error.
        """
        try:
            resp = self._client.post(
                "/portal/object/frontend/getallobjects/format/json"
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])
        except Exception as e:
            logger.error("get_listings failed: %s", e)
            return []

    def get_active_reactions(self) -> set[str]:
        """Return object ids for all current active applications on this account.

        Walks the response defensively — never raises, returns empty set on error.
        """
        try:
            resp = self._client.post(
                "/portal/registration/frontend/getactievereacties/format/json"
            )
            resp.raise_for_status()
            data = resp.json()
            return self._extract_ids(data)
        except Exception as e:
            logger.warning("get_active_reactions failed: %s", e)
            return set()

    def login(
        self,
        username: str,
        password: str,
        session_cookie: Optional[str] = None,
        client_id: str = "wzp",
    ) -> bool:
        """Authenticate with the portal.

        Cookie fallback (fastest): if *session_cookie* is provided and non-empty,
        inject it via _inject_cookies and verify with is_logged_in().

        Primary path — OAuth2 password grant to auth.hexia.io:
          1. POST https://auth.hexia.io/api/v1/oauth/token with JSON creds.
          2. Set the returned access token as Bearer header.
          3. POST /portal/account/frontend/loginbyservice/format/json to
             convert the OAuth token into a plaza session cookie.
          4. Return is_logged_in().

        Returns True if a session is established, False otherwise.
        """
        # --- Cookie fallback ---
        if session_cookie and session_cookie.strip():
            logger.info("Using provided PLAZA_SESSION_COOKIE")
            self._inject_cookies(session_cookie)
            result = self.is_logged_in()
            if result:
                logger.info("Session cookie verified — logged in")
            else:
                logger.warning("Session cookie injected but is_logged_in() returned False")
            return result

        # --- OAuth2 password grant ---
        logger.info("Attempting OAuth2 password grant for user %s", username)
        auth_url = "https://auth.hexia.io/api/v1/oauth/token"
        try:
            resp = self._client.post(
                auth_url,
                json={
                    "client_id": client_id,
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Origin": "https://plaza.newnewnew.space",
                    "Referer": "https://plaza.newnewnew.space/",
                },
            )
            data = resp.json()
        except Exception as e:
            logger.error("OAuth token request failed: %s", e)
            return False

        # Handle error responses
        if "error" in data:
            err = data["error"]
            desc = data.get("error_description") or err
            if err == "invalid_client":
                logger.error(
                    "OAuth error: %s — client_id '%s' rejected by auth server. "
                    "Capture the real client_id from browser DevTools "
                    "(Network → request to auth.hexia.io/api/v1/oauth/token → "
                    "request payload) and set PLAZA_CLIENT_ID in .env.",
                    desc,
                    client_id,
                )
            elif err == "mfa_required" or "mfa_token" in data:
                logger.error(
                    "Account requires MFA — automatic login not supported; "
                    "use PLAZA_SESSION_COOKIE fallback."
                )
            else:
                logger.error("OAuth error: %s", desc)
            return False

        if "mfa_token" in data:
            logger.error(
                "Account requires MFA — automatic login not supported; "
                "use PLAZA_SESSION_COOKIE fallback."
            )
            return False

        access_token = data.get("accessToken") or data.get("access_token")
        if not access_token:
            logger.error(
                "OAuth response contained no access token. Response: %.300s",
                str(data),
            )
            return False

        # Store access token on the shared client
        self._client.headers["Authorization"] = f"Bearer {access_token}"
        logger.info("OAuth access token obtained; starting portal session")

        # Convert OAuth token → plaza session cookie
        try:
            portal_resp = self._client.post(
                "/portal/account/frontend/loginbyservice/format/json",
                headers={"Accept": "application/json"},
            )
            logger.debug(
                "loginbyservice status=%d body=%.200s",
                portal_resp.status_code,
                portal_resp.text,
            )
        except Exception as e:
            logger.error("loginbyservice request failed: %s", e)
            return False

        return self.is_logged_in()

    def is_logged_in(self) -> bool:
        """Return True if the current session appears to be authenticated."""
        try:
            resp = self._client.post(
                "/portal/account/frontend/getaccount/format/json"
            )
            if resp.status_code >= 400:
                return False
            data = resp.json()
            # A real account response has account-ish keys; an error/guest
            # response typically has an "error" key or an empty result.
            if isinstance(data, dict):
                if data.get("error") or data.get("status") == "error":
                    return False
                # Look for any key that suggests a real account object
                account_keys = {"id", "email", "gebruikersnaam", "voornaam", "achternaam", "name"}
                result = data.get("result") or data
                if isinstance(result, dict) and account_keys.intersection(result.keys()):
                    return True
                # If result is a non-empty dict without obvious error, be lenient
                if isinstance(result, dict) and result:
                    return True
            return False
        except Exception as e:
            logger.debug("is_logged_in check failed: %s", e)
            return False

    def react(
        self, object_id: str, object_type: str = "woning"
    ) -> tuple[bool, str]:
        """Submit an application for a listing.

        Args:
            object_id: The listing id (e.g. "13106").
            object_type: Always "woning" per the verified API.

        Returns:
            (success, response_text_snippet) — success is True when the
            response is 2xx and does not look like an error response.
        """
        try:
            resp = self._client.post(
                "/portal/object/frontend/react/format/json",
                json={"objectType": object_type, "objectId": object_id},
            )
            snippet = resp.text[:300]
            if resp.status_code >= 400:
                return False, snippet
            # Check for error indicators in the body
            try:
                body = resp.json()
                if isinstance(body, dict) and (
                    body.get("error")
                    or body.get("status") == "error"
                    or body.get("success") is False
                ):
                    return False, snippet
            except Exception:
                pass
            return True, snippet
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _inject_cookies(self, cookie_header: str) -> None:
        """Parse a raw Cookie header string and add cookies to the client jar."""
        for pair in cookie_header.split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, _, value = pair.partition("=")
                self._client.cookies.set(name.strip(), value.strip())

    @staticmethod
    def _extract_ids(data: object) -> set[str]:
        """Walk arbitrary JSON and collect values of id-like keys."""
        ids: set[str] = set()
        _ID_KEYS = {"objectId", "id", "advertentieId", "objectid"}

        def _walk(node: object) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in _ID_KEYS and v is not None:
                        ids.add(str(v))
                    else:
                        _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(data)
        return ids
