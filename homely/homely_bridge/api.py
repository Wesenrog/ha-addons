"""Client for the Homely SDK REST API (sdk.iotiliti.cloud)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://sdk.iotiliti.cloud/homely/"

# Statuses that mean "try again later" rather than "your request is wrong".
# 439 is not a standard HTTP status but Homely has been seen returning it.
TRANSIENT_STATUSES = {408, 425, 429, 439, 500, 502, 503, 504}


class HomelyError(Exception):
    """Base error for the Homely API."""


class HomelyAuthError(HomelyError):
    """Credentials or tokens were rejected."""


class HomelyUnavailable(HomelyError):
    """The API is rate limited, down or unreachable. Retry later."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class HomelyApiError(HomelyError):
    """Any other unexpected API response."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _retry_after(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _error_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "message" in body:
        message = body["message"]
        return "; ".join(message) if isinstance(message, list) else str(message)
    return str(body)[:200]


class HomelyAPI:
    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        session: requests.Session | None = None,
    ):
        self._username = username
        self._password = password
        self._base_url = base_url if base_url.endswith("/") else base_url + "/"
        self._timeout = timeout
        self._session = session or requests.Session()
        self._lock = threading.RLock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._refresh_expires_at = 0.0
        self._lifetime = 0.0

    # -- tokens ---------------------------------------------------------------

    def _store_tokens(self, payload: Any) -> None:
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise HomelyApiError("Token response did not contain an access token")
        now = time.time()
        self._access_token = payload["access_token"]
        self._refresh_token = payload.get("refresh_token")
        self._lifetime = float(payload.get("expires_in") or 60)
        self._expires_at = now + self._lifetime
        refresh_lifetime = payload.get("refresh_expires_in")
        self._refresh_expires_at = now + float(refresh_lifetime) if refresh_lifetime else 0.0
        _LOGGER.debug(
            "Got access token valid for %ds (refresh token %ss)", self._lifetime, refresh_lifetime
        )

    def _post_token(self, endpoint: str, body: dict[str, str]) -> None:
        url = self._base_url + endpoint
        try:
            response = self._session.post(url, json=body, timeout=self._timeout)
        except requests.RequestException as err:
            raise HomelyUnavailable(f"Could not reach Homely: {err}") from err
        if response.status_code in (200, 201):
            try:
                payload = response.json()
            except ValueError as err:
                raise HomelyApiError("Token response was not JSON", response.status_code) from err
            self._store_tokens(payload)
            return
        if response.status_code in TRANSIENT_STATUSES:
            raise HomelyUnavailable(
                f"{endpoint} returned {response.status_code}",
                response.status_code,
                _retry_after(response),
            )
        if response.status_code in (400, 401, 403):
            raise HomelyAuthError(f"{endpoint} rejected: {_error_message(response)}")
        raise HomelyApiError(
            f"{endpoint} returned {response.status_code}: {_error_message(response)}",
            response.status_code,
        )

    def login(self) -> None:
        with self._lock:
            _LOGGER.debug("Logging in to Homely")
            self._post_token("oauth/token", {"username": self._username, "password": self._password})

    def _refresh(self) -> None:
        self._post_token("oauth/refresh-token", {"refresh_token": self._refresh_token or ""})

    def token(self) -> str:
        """Return a valid access token, refreshing or logging in as needed."""
        with self._lock:
            now = time.time()
            margin = max(5.0, min(300.0, self._lifetime * 0.2))
            if self._access_token and now < self._expires_at - margin:
                return self._access_token
            refresh_usable = self._refresh_token and (
                not self._refresh_expires_at or now < self._refresh_expires_at - 5
            )
            if refresh_usable:
                try:
                    self._refresh()
                    return self._access_token  # type: ignore[return-value]
                except HomelyAuthError as err:
                    _LOGGER.info("Token refresh rejected (%s), logging in again", err)
            self.login()
            return self._access_token  # type: ignore[return-value]

    def invalidate_token(self) -> None:
        with self._lock:
            self._access_token = None
            self._expires_at = 0.0

    # -- data -----------------------------------------------------------------

    def _get(self, endpoint: str) -> Any:
        for attempt in (1, 2):
            token = self.token()
            url = self._base_url + endpoint
            try:
                response = self._session.get(
                    url, headers={"Authorization": f"Bearer {token}"}, timeout=self._timeout
                )
            except requests.RequestException as err:
                raise HomelyUnavailable(f"Could not reach Homely: {err}") from err
            status = response.status_code
            if status in (200, 201):
                try:
                    return response.json()
                except ValueError as err:
                    raise HomelyApiError(f"{endpoint} response was not JSON", status) from err
            if status == 401 and attempt == 1:
                _LOGGER.info("Access token rejected, logging in again")
                self.invalidate_token()
                with self._lock:
                    self._refresh_token = None
                continue
            if status in TRANSIENT_STATUSES:
                raise HomelyUnavailable(f"{endpoint} returned {status}", status, _retry_after(response))
            if status in (401, 403):
                raise HomelyAuthError(f"{endpoint} rejected: {_error_message(response)}")
            raise HomelyApiError(f"{endpoint} returned {status}: {_error_message(response)}", status)
        raise HomelyAuthError(f"{endpoint} rejected after logging in again")

    def get_locations(self) -> list[dict[str, Any]]:
        data = self._get("locations")
        if not isinstance(data, list):
            raise HomelyApiError("Locations response was not a list")
        return data

    def get_home(self, location_id: str) -> dict[str, Any]:
        data = self._get(f"home/{location_id}")
        if not isinstance(data, dict) or not isinstance(data.get("devices", []), list):
            raise HomelyApiError("Home response had an unexpected format")
        return data
