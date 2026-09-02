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

Session persistence — if a session_file is passed to login(), the client will
  try to reuse a previously saved session (cookies + tokens) before attempting a
  full password grant.  On any successful auth the session is saved back.
  Stale cookies from a saved session are discarded before any fresh grant,
  because the portal rejects loginbyservice when they are present.

is_logged_in() and react() work once either path succeeds (Bearer header + session
cookie are both carried on subsequent requests by the shared httpx.Client).

Every ``/portal/**/format/json`` response also carries a top-level
``sAngularServiceData`` key — an Angular bootstrap config string the portal
attaches to *every* response, success or failure. The real payload always
sits in a sibling key (e.g. ``account``, ``result``). Its presence must never
be treated as an error indicator; see ``_payload()``.

Applying (react)
-----------------
The real frontend applies to a listing in three steps:
  1. GET  /portal/core/frontend/getformsubmitonlyconfiguration/format/json —
     yields a session-bound ``__hash__`` CSRF token (``_form_hash()``).
  2. POST /portal/object/frontend/getreagerendata/format/json with
     ``objectId[]=<id>`` — yields, per object, whether it can be reacted to
     and a ``url`` such as ``?add=10320&dwellingID=15191`` whose query
     parameters are the actual react request body
     (``get_reageren_data()``).
  3. POST /portal/object/frontend/react/format/json with those parameters
     plus ``__hash__`` from step 1.

All ``/portal/**`` endpoints require form-urlencoded bodies (``objectId[]=..``
style), never JSON — a JSON body makes some endpoints (e.g. ``getobject``)
answer with ``{"sMessage": "Onbekende fout"}``.
"""

import json
import logging
import os
from typing import Optional
from urllib.parse import parse_qsl

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://plaza.newnewnew.space"
OAUTH_TOKEN_PATH = "/portal/proxy/frontend/api/v1/oauth/token"

_FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Origin": "https://plaza.newnewnew.space",
    "Referer": "https://plaza.newnewnew.space/",
}


class RateLimited(Exception):
    """Raised when the server responds with HTTP 429 or 403."""


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


def _payload(body: object) -> dict:
    """Strip the Angular bootstrap config the portal attaches to every response.

    Portal endpoints always return ``sAngularServiceData`` alongside the real
    payload; its presence says nothing about success or failure.
    """
    if isinstance(body, dict):
        return {k: v for k, v in body.items() if k != "sAngularServiceData"}
    return {}


class PlazaClient:
    """Thin HTTP wrapper around the plaza.newnewnew.space portal API."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers=_DEFAULT_HEADERS,
            timeout=30,
            follow_redirects=True,
        )
        self._refresh_token: Optional[str] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_listings(self) -> list[dict]:
        """Fetch all listings (no auth required).

        Returns the list under the ``result`` key, or an empty list on error.
        Raises RateLimited if the server responds with HTTP 429 or 403.
        """
        try:
            resp = self._client.post(
                "/portal/object/frontend/getallobjects/format/json"
            )
            if resp.status_code in (429, 403):
                raise RateLimited(resp.status_code)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])
        except RateLimited:
            raise
        except Exception as e:
            logger.error("get_listings failed: %s", e)
            return []

    def get_active_reactions(self) -> set[str]:
        """Return object ids for all current active applications on this account.

        Reads ``result.items[].object.id`` from the response. Defensive —
        never raises, returns empty set on error.
        """
        try:
            resp = self._client.post(
                "/portal/registration/frontend/getactievereacties/format/json",
                json={},
            )
            resp.raise_for_status()
            items = ((_payload(resp.json()).get("result") or {}).get("items")) or []
            ids = set()
            for item in items:
                obj_id = (item.get("object") or {}).get("id")
                if obj_id is not None:
                    ids.add(str(obj_id))
            return ids
        except Exception as e:
            logger.warning("get_active_reactions failed: %s", e)
            return set()

    def get_reageren_data(self, object_ids: list[str]) -> dict[str, dict]:
        """Return the portal's per-object reaction metadata, keyed by object id.

        Each entry carries ``kanReageren``, ``action`` ("add" or "remove") and
        ``url`` — a query string such as ``?add=10320&dwellingID=15191`` whose
        parameters form the body of the actual react request.
        """
        try:
            resp = self._client.post(
                "/portal/object/frontend/getreagerendata/format/json",
                data={"objectId[]": [str(i) for i in object_ids]},
                headers=_FORM_HEADERS,
            )
            resp.raise_for_status()
            return _payload(resp.json()).get("reagerenData") or {}
        except Exception as e:
            logger.warning("get_reageren_data failed: %s", e)
            return {}

    def login(
        self,
        username: str,
        password: str,
        session_cookie: Optional[str] = None,
        client_id: str = "wzp",
        session_file: Optional[str] = None,
    ) -> bool:
        """Authenticate with the portal.

        Precedence:
          1. Cookie fallback: if *session_cookie* is provided and non-empty,
             inject it and verify with is_logged_in().
          2. Session reuse: if *session_file* is provided and the file exists,
             load cookies + tokens; if already logged in, return True without
             hitting the password grant.
          3. Refresh-token: if a refresh token was loaded from the session file,
             try a refresh grant; on success save and return True.
          4. OAuth2 password grant: full login; on success save and return True.

        Returns True if a session is established, False otherwise.
        """
        # 1. Cookie fallback
        if session_cookie and session_cookie.strip():
            logger.info("Using provided PLAZA_SESSION_COOKIE")
            self._inject_cookies(session_cookie)
            result = self.is_logged_in()
            if result:
                logger.info("Session cookie verified — logged in")
                if session_file:
                    self.save_session(session_file)
            else:
                logger.warning("Session cookie injected but is_logged_in() returned False")
            return result

        # 2. Attempt to reuse a persisted session
        if session_file and self.load_session(session_file):
            if self.is_logged_in():
                logger.info("Reused saved session")
                return True

            # 3. Try refresh-token grant
            if self._refresh_token:
                logger.info("Saved session expired; attempting token refresh")
                if self._refresh(client_id):
                    if session_file:
                        self.save_session(session_file)
                    return True
                logger.warning("Token refresh failed; falling back to password grant")

        # 4. OAuth2 password grant
        self._reset_auth_state()
        logger.info("Attempting OAuth2 password grant for user %s", username)
        try:
            resp = self._client.post(
                OAUTH_TOKEN_PATH,
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

        # Store access token and optional refresh token on the shared client
        self._client.headers["Authorization"] = f"Bearer {access_token}"
        refresh_token = data.get("refreshToken") or data.get("refresh_token")
        if refresh_token:
            self._refresh_token = refresh_token
        logger.info("OAuth access token obtained; starting portal session")

        # Convert OAuth token → plaza session cookie
        try:
            portal_resp = self._client.post(
                "/portal/account/frontend/loginbyservice/format/json",
                headers={"Accept": "application/json"},
            )
            if portal_resp.status_code >= 400:
                logger.warning(
                    "loginbyservice failed: status=%d body=%.200s",
                    portal_resp.status_code,
                    portal_resp.text,
                )
            else:
                logger.debug(
                    "loginbyservice status=%d body=%.200s",
                    portal_resp.status_code,
                    portal_resp.text,
                )
        except Exception as e:
            logger.error("loginbyservice request failed: %s", e)
            return False

        result = self.is_logged_in()
        if result and session_file:
            self.save_session(session_file)
        return result

    def is_logged_in(self) -> bool:
        """Return True if the current session appears to be authenticated."""
        try:
            resp = self._client.post(
                "/portal/account/frontend/getaccount/format/json"
            )
            if resp.status_code >= 400:
                return False
            account = _payload(resp.json()).get("account")
            if isinstance(account, dict) and (
                account.get("username") or account.get("persons")
            ):
                return True
            return False
        except Exception as e:
            logger.debug("is_logged_in check failed: %s", e)
            return False

    def react(
        self, object_id: str, object_type: str = "woning"
    ) -> tuple[bool, str]:
        """Submit an application for a listing.

        The real request body is not ``objectId`` — it is ``__hash__`` (a
        session-bound CSRF token from ``_form_hash()``) plus the parameters
        parsed out of the object's reaction ``url`` (from
        ``get_reageren_data()``), e.g. ``add=<toewijzingID>&dwellingID=<objectId>``.

        Args:
            object_id: The listing id (e.g. "13106").
            object_type: Kept for signature compatibility only; not used.

        Returns:
            (success, response_text_snippet) — success is True when the
            portal confirms the reaction was processed.

        Raises:
            RateLimited: if the server responds with HTTP 429 or 403.
        """
        try:
            object_id = str(object_id)
            info = self.get_reageren_data([object_id]).get(object_id)
            if not info:
                return False, f"no reageren data for object {object_id}"
            action = info.get("action")
            if action == "remove":
                # A reaction already exists; the same endpoint would withdraw it.
                return True, "already applied"
            if action != "add":
                return False, f"unexpected reaction action {action!r}"
            if not info.get("kanReageren"):
                return False, "portal says kanReageren=false for this object"
            params = dict(parse_qsl((info.get("url") or "").lstrip("?")))
            if "add" not in params:
                return False, f"no add parameter in reaction url {info.get('url')!r}"
            form_hash = self._form_hash()
            if form_hash:
                params["__hash__"] = form_hash
            else:
                logger.warning("Could not obtain form __hash__; react will likely fail")

            resp = self._client.post(
                "/portal/object/frontend/react/format/json",
                data=params,
                headers=_FORM_HEADERS,
            )
            if resp.status_code in (429, 403):
                raise RateLimited(resp.status_code)
            if resp.status_code >= 400:
                return False, resp.text[:300]
            try:
                parsed = resp.json()
            except Exception:
                return False, resp.text[:300]

            body = _payload(parsed)
            try:
                snippet = json.dumps(body)[:300]
            except Exception:
                snippet = str(body)[:300]

            # Explicit success indicators
            if body.get("success") is True or (
                {"reactionId", "reactionData", "numberOfReactions"} & body.keys()
            ):
                return True, snippet

            # Not obviously a success — authoritative re-check against the
            # account's active reactions before declaring failure.
            if object_id in self.get_active_reactions():
                return True, "confirmed via active reactions"

            message = body.get("sMessage") or body.get("aErrorCodes") or snippet
            return False, str(message)[:300]
        except RateLimited:
            raise
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def save_session(self, path: str) -> None:
        """Persist cookies, access token, and refresh token to *path* as JSON."""
        cookies = {name: value for name, value in self._client.cookies.items()}
        access_token: Optional[str] = None
        auth_header = self._client.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            access_token = auth_header[len("Bearer "):]
        data = {
            "cookies": cookies,
            "access_token": access_token,
            "refresh_token": self._refresh_token,
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug("Session saved to %s", path)
        except Exception as e:
            logger.warning("Could not save session to %s: %s", path, e)

    def load_session(self, path: str) -> bool:
        """Load cookies and tokens from *path*.

        Returns True if any data was loaded, False if the file is missing or
        empty/corrupt.
        """
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Could not load session from %s: %s", path, e)
            return False

        loaded_anything = False

        cookies: dict = data.get("cookies") or {}
        for name, value in cookies.items():
            self._client.cookies.set(name, value)
            loaded_anything = True

        access_token = data.get("access_token")
        if access_token:
            self._client.headers["Authorization"] = f"Bearer {access_token}"
            loaded_anything = True

        refresh_token = data.get("refresh_token")
        if refresh_token:
            self._refresh_token = refresh_token
            loaded_anything = True

        if loaded_anything:
            logger.debug("Session loaded from %s", path)
        return loaded_anything

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _form_hash(self) -> Optional[str]:
        """Fetch the session-bound ``__hash__`` CSRF token the portal forms require."""
        try:
            resp = self._client.get(
                "/portal/core/frontend/getformsubmitonlyconfiguration/format/json"
            )
            resp.raise_for_status()
            elements = (
                _payload(resp.json()).get("form", {}).get("elements", {})
            )
            hash_element = elements.get("__hash__")
            if isinstance(hash_element, dict):
                return hash_element.get("initialData")
            return None
        except Exception as e:
            logger.warning("_form_hash failed: %s", e)
            return None

    def _reset_auth_state(self) -> None:
        """Drop all cookies and tokens from the shared client.

        Stale cookies loaded from a persisted session make the portal's
        loginbyservice endpoint fail with HTTP 500 even when a freshly issued
        OAuth token is present, so the jar must be emptied before any fresh
        grant.
        """
        self._client.cookies.clear()
        self._client.headers.pop("Authorization", None)
        self._refresh_token = None

    def _refresh(self, client_id: str) -> bool:
        """Attempt an OAuth2 refresh-token grant.

        On success: updates the Authorization header, stores the new refresh
        token, calls loginbyservice, and returns is_logged_in().
        On failure: returns False.
        """
        if not self._refresh_token:
            return False
        try:
            resp = self._client.post(
                OAUTH_TOKEN_PATH,
                json={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
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
            logger.warning("Refresh-token request failed: %s", e)
            return False

        if "error" in data:
            logger.warning("Refresh-token error: %s", data.get("error_description") or data["error"])
            return False

        access_token = data.get("accessToken") or data.get("access_token")
        if not access_token:
            logger.warning("Refresh-token response contained no access token")
            return False

        self._client.headers["Authorization"] = f"Bearer {access_token}"
        new_refresh = data.get("refreshToken") or data.get("refresh_token")
        if new_refresh:
            self._refresh_token = new_refresh

        # Stale cookies from the previous session break loginbyservice, so
        # drop them before converting the refreshed OAuth token.
        self._client.cookies.clear()

        # Convert refreshed OAuth token → plaza session cookie
        try:
            self._client.post(
                "/portal/account/frontend/loginbyservice/format/json",
                headers={"Accept": "application/json"},
            )
        except Exception as e:
            logger.warning("loginbyservice after refresh failed: %s", e)

        return self.is_logged_in()

    def _inject_cookies(self, cookie_header: str) -> None:
        """Parse a raw Cookie header string and add cookies to the client jar."""
        for pair in cookie_header.split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, _, value = pair.partition("=")
                self._client.cookies.set(name.strip(), value.strip())

