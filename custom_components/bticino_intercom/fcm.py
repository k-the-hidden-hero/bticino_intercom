"""Native Android FCM push receiver for BTicino/Netatmo RTC call offers.

Newer BTicino bridges (EasyKit BDIY, Classe 300 EOS BNCX, and Classe 100X16E
BNC1 hardware_version 200 / fw 2.03.11) deliver incoming WebRTC SDP offers
(*-rtc) exclusively over FCM, never over the Netatmo push WebSocket (/ws/).

This module:
1. Registers a native Android FCM token via Google's c2dm/register3 endpoint
   using the Home + Security app's Firebase identifiers and signing cert SHA-1
   (WebPush registration causes Google to strip Netatmo's android.data payload).
2. Persists the GCM/FCM credentials in HA Store so restarts re-checkin instead
   of registering a new receiver on every boot.
3. Registers the token with Netatmo via POST /api/addpushcontext (and drops it
   via POST /api/droppushcontext when the config entry is permanently removed).
4. Works around the firebase-messaging 0.4.5 in-place reset spin-loop bug by
   ending the client on the first stream error and reconnecting cleanly from an
   outer loop with exponential backoff (5s -> 300s).
5. Forwards *-rtc pushes directly into the coordinator's push handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import aiohttp
from firebase_messaging import FcmPushClient, FcmPushClientConfig, FcmRegisterConfig
from firebase_messaging.const import GCM_REGISTER_URL
from firebase_messaging.fcmregister import FcmRegister
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from pybticino import AuthHandler
from pybticino.const import BASE_URL, build_user_agent

from .const import DOMAIN, ENV_FCM_API_KEY, OPT_FCM_API_KEY

_LOGGER = logging.getLogger(__name__)

# Official Home + Security Android app (com.netatmo.camera 4.1.1.3) identifiers.
# Kept together in one block so updating after an app release is a single edit.
# The Firebase Android API key (from google-services.json inside com.netatmo.camera)
# is intentionally not hardcoded in source; configure it via the integration's
# 'fcm_api_key' option or the BTICINO_FCM_API_KEY environment variable.
ANDROID_PACKAGE = "com.netatmo.camera"
ANDROID_CERT_SHA1 = "CDAF5D982A79B76743546599A87F7C45BAC269D8"
ANDROID_APP_VERSION_CODE = "4401103"
ANDROID_APP_VERSION_NAME = "4.1.1.3"
FIREBASE_PROJECT_ID = "security-7228d"
FIREBASE_APP_ID = "1:514810499773:android:ed5e6854097137cb"
FIREBASE_MESSAGING_SENDER_ID = "514810499773"
ANDROID_HEADERS = {
    "X-Android-Package": ANDROID_PACKAGE,
    "X-Android-Cert": ANDROID_CERT_SHA1,
}

ADD_PUSH_URL = f"{BASE_URL}/api/addpushcontext"
DROP_PUSH_URL = f"{BASE_URL}/api/droppushcontext"

FCM_STORAGE_VERSION = 1
FCM_BACKOFF_INITIAL = 5.0
FCM_BACKOFF_MAX = 300.0
FCM_STABLE_SECONDS = 120.0
FCM_REGISTER_ATTEMPTS = 4
FCM_REGISTER_RETRY_DELAY = 1.5


def resolve_fcm_api_key(entry: ConfigEntry) -> str:
    """Return the configured Firebase Android API key from entry options or environment."""
    option_val = entry.options.get(OPT_FCM_API_KEY)
    if isinstance(option_val, str) and option_val.strip():
        return option_val.strip()
    return os.environ.get(ENV_FCM_API_KEY, "").strip()


def build_firebase_config(api_key: str) -> FcmRegisterConfig:
    """Build FcmRegisterConfig for com.netatmo.camera using the configured API key."""
    return FcmRegisterConfig(
        project_id=FIREBASE_PROJECT_ID,
        app_id=FIREBASE_APP_ID,
        api_key=api_key,
        messaging_sender_id=FIREBASE_MESSAGING_SENDER_ID,
    )


class _SafeAndroidFcmClient(FcmPushClient):
    """FcmPushClient subclass for native Android MCS pushes without spin-loop reset.

    Native Android c2dm/register3 pushes arrive as cleartext key/value pairs in
    DataMessageStanza.app_data (inside the MCS TLS tunnel), rather than WebPush
    aesgcm raw_data.

    Overriding _reset() to terminate immediately avoids the firebase-messaging
    0.4.5 bug where _listen() spins on a dead StreamReader while _reset() holds
    reset_lock, starving Home Assistant's event loop.
    """

    def _handle_data_message(self, msg: Any) -> None:
        app_map = {x.key: x.value for x in msg.app_data}
        if app_map.get("message_type") == "deleted_messages":
            return
        try:
            self.callback(app_map, msg.persistent_id, self.callback_context)
        except Exception:
            _LOGGER.exception("FCM callback raised an unexpected error")

    async def _reset(self) -> None:
        _LOGGER.debug("FCM stream error/reset requested; ending client for clean outer reconnect")
        self._terminate()


async def _register_android_fcm(
    fcm_http: aiohttp.ClientSession,
    firebase_config: FcmRegisterConfig,
) -> tuple[str, dict[str, Any]]:
    """Register a native Android FCM token via Google checkin + FIS + c2dm/register3."""
    reg = FcmRegister(firebase_config, http_client_session=fcm_http)
    checkin = await reg.gcm_check_in()
    if not checkin:
        raise RuntimeError("GCM checkin failed")
    android_id = str(checkin["androidId"])
    security_token = str(checkin["securityToken"])

    installation = await reg.fcm_install()
    if not installation:
        raise RuntimeError("Firebase installations (FIS) registration failed")

    headers = {
        "Authorization": f"AidLogin {android_id}:{security_token}",
        "app": ANDROID_PACKAGE,
        "X-Goog-Firebase-Installations-Auth": installation["token"],
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Android-GCM/1.5",
    }
    body = {
        "app": ANDROID_PACKAGE,
        "sender": firebase_config.messaging_sender_id,
        "device": android_id,
        "cert": ANDROID_CERT_SHA1.lower(),
        "app_ver": ANDROID_APP_VERSION_CODE,
        "X-app_ver_name": ANDROID_APP_VERSION_NAME,
        "gcm_ver": "221808000",
        "X-osv": "33",
        "X-subtype": firebase_config.messaging_sender_id,
        "X-gmp_app_id": firebase_config.app_id,
        "X-scope": "*",
        "X-appid": installation["fid"],
        "X-Goog-Firebase-Installations-Auth": installation["token"],
    }

    last_resp = ""
    for attempt in range(1, FCM_REGISTER_ATTEMPTS + 1):
        if attempt > 1:
            await asyncio.sleep(FCM_REGISTER_RETRY_DELAY)
        async with fcm_http.post(GCM_REGISTER_URL, headers=headers, data=body, timeout=15) as resp:
            last_resp = await resp.text()
        if last_resp.startswith("token="):
            fcm_token = last_resp.split("=", 1)[1].strip()
            credentials: dict[str, Any] = {
                "gcm": {
                    "android_id": android_id,
                    "security_token": security_token,
                    "app_id": ANDROID_PACKAGE,
                },
                "fcm": {
                    "registration": {"token": fcm_token},
                    "installation": installation,
                },
            }
            return fcm_token, credentials
        _LOGGER.debug(
            "Android c2dm/register3 attempt %d/%d failed: %s", attempt, FCM_REGISTER_ATTEMPTS, last_resp.strip()
        )

    raise RuntimeError(f"Android c2dm/register3 failed after {FCM_REGISTER_ATTEMPTS} attempts: {last_resp.strip()}")


async def _post_netatmo_push(
    session: aiohttp.ClientSession,
    auth_handler: AuthHandler,
    url: str,
    payload: dict[str, Any],
) -> None:
    token = await auth_handler.get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": build_user_agent(),
        "Content-Type": "application/json; charset=utf-8",
    }
    async with session.post(url, json=payload, headers=headers, timeout=20) as resp:
        text = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"{url} returned HTTP {resp.status}: {text[:200]}")
        _LOGGER.debug("%s -> HTTP 200: %s", url, text[:200])


async def async_drop_fcm_registration(
    hass: HomeAssistant,
    entry: ConfigEntry,
    auth_handler: AuthHandler | None = None,
) -> None:
    """Drop the stored FCM push context from Netatmo and delete local credentials."""
    store = Store(hass, FCM_STORAGE_VERSION, f"{DOMAIN}.fcm.{entry.entry_id}")
    saved = await store.async_load()
    if isinstance(saved, dict) and (fcm_token := saved.get("fcm_token")) and auth_handler is not None:
        session = async_get_clientsession(hass)
        payload = {
            "credentials": json.dumps({"device_token": fcm_token}),
            "type": "Android",
            "app_version": ANDROID_APP_VERSION_NAME,
            "app_type": "app_camera",
        }
        try:
            await _post_netatmo_push(session, auth_handler, DROP_PUSH_URL, payload)
            _LOGGER.info("Dropped Netatmo FCM push context for entry %s", entry.entry_id)
        except Exception as err:
            _LOGGER.warning("Could not drop Netatmo FCM push context for entry %s: %s", entry.entry_id, err)
    await store.async_remove()


class BticinoFcmReceiver:
    """Manage the persistent Android FCM listener for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        auth_handler: AuthHandler,
        message_callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        api_key: str | None = None,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._auth = auth_handler
        self._message_callback = message_callback
        self._api_key = (api_key if api_key is not None else resolve_fcm_api_key(entry)).strip()
        self._store = Store(hass, FCM_STORAGE_VERSION, f"{DOMAIN}.fcm.{entry.entry_id}")
        self._credentials: dict[str, Any] | None = None
        self._fcm_token: str | None = None
        self._registered_with_netatmo = False

    @property
    def enabled(self) -> bool:
        """Return True when a Firebase Android API key is configured."""
        return bool(self._api_key)

    def _on_fcm_message(self, app_map: dict[str, Any], persistent_id: str, context: Any) -> None:
        push_type = str(app_map.get("push_type") or "")
        if not push_type.endswith("-rtc"):
            _LOGGER.debug("FCM non-RTC push ignored (handled via WS): push_type=%s", push_type)
            return

        raw_extra = app_map.get("extra_params")
        extra_params: dict[str, Any]
        if isinstance(raw_extra, dict):
            extra_params = raw_extra
        elif isinstance(raw_extra, str):
            try:
                parsed = json.loads(raw_extra)
                extra_params = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                _LOGGER.warning("FCM %s push had unparseable extra_params", push_type)
                return
        else:
            extra_params = {}

        rtc_action = (
            (extra_params.get("data") or {}).get("type") if isinstance(extra_params.get("data"), dict) else None
        )
        _LOGGER.info(
            "FCM RTC push received: push_type=%s action=%s session_id=%s",
            push_type,
            rtc_action,
            extra_params.get("session_id"),
        )

        normalized: dict[str, Any] = {
            "type": "FCM",
            "source": "fcm",
            "push_type": push_type,
            "extra_params": extra_params,
        }
        if alert := app_map.get("alert"):
            normalized["alert"] = alert
        if category := app_map.get("category"):
            normalized["category"] = category

        self._hass.async_create_task(
            self._message_callback(normalized),
            name=f"{DOMAIN} FCM push handler - {push_type}",
        )

    async def _ensure_registered(self, firebase_config: FcmRegisterConfig) -> tuple[str, dict[str, Any]]:
        """Check in with saved credentials or register fresh, then call addpushcontext."""
        if self._credentials is None or self._fcm_token is None:
            saved = await self._store.async_load()
            if isinstance(saved, dict) and saved.get("credentials") and saved.get("fcm_token"):
                self._credentials = saved["credentials"]
                self._fcm_token = saved["fcm_token"]

        async with aiohttp.ClientSession(headers=ANDROID_HEADERS) as fcm_http:
            if self._credentials and self._fcm_token:
                reg = FcmRegister(firebase_config, credentials=self._credentials, http_client_session=fcm_http)
                gcm = self._credentials.get("gcm", {})
                try:
                    checkin_ok = await reg.gcm_check_in(int(gcm["android_id"]), int(gcm["security_token"]))
                except Exception as err:
                    _LOGGER.debug("Stored FCM checkin failed (%s); re-registering", err)
                    checkin_ok = None
                if not checkin_ok:
                    self._credentials = None
                    self._fcm_token = None
                    self._registered_with_netatmo = False

            if not self._credentials or not self._fcm_token:
                _LOGGER.info("Registering native Android FCM receiver for %s", self._entry.entry_id)
                fcm_token, credentials = await _register_android_fcm(fcm_http, firebase_config)
                self._fcm_token = fcm_token
                self._credentials = credentials
                self._registered_with_netatmo = False
                await self._store.async_save({"fcm_token": fcm_token, "credentials": credentials})

        if not self._registered_with_netatmo:
            session = async_get_clientsession(self._hass)
            payload = {
                "credentials": json.dumps({"device_token": self._fcm_token}),
                "device_version": "HomeAssistant",
                "os_version": "13",
                "type": "Android",
                "app_version": ANDROID_APP_VERSION_CODE,
                "app_type": "app_camera",
            }
            await _post_netatmo_push(session, self._auth, ADD_PUSH_URL, payload)
            self._registered_with_netatmo = True
            _LOGGER.info("Netatmo FCM push context registered (HomeAssistant)")

        assert self._fcm_token is not None and self._credentials is not None
        return self._fcm_token, self._credentials

    async def run_forever(self) -> None:
        """Keep the FCM MCS connection alive with exponential backoff."""
        if not self._api_key:
            _LOGGER.info(
                "Native Android FCM receiver disabled: configure '%s' in options or set %s.",
                OPT_FCM_API_KEY,
                ENV_FCM_API_KEY,
            )
            return

        firebase_config = build_firebase_config(self._api_key)
        backoff = FCM_BACKOFF_INITIAL
        try:
            while self._hass.state != CoreState.stopping and self._hass.is_running:
                started_at = time.monotonic()
                client: _SafeAndroidFcmClient | None = None
                try:
                    _, credentials = await self._ensure_registered(firebase_config)
                    client = _SafeAndroidFcmClient(
                        callback=self._on_fcm_message,
                        fcm_config=firebase_config,
                        credentials=credentials,
                        config=FcmPushClientConfig(abort_on_sequential_error_count=1),
                    )
                    await client.start()
                    if client.tasks:
                        done, pending = await asyncio.wait(
                            client.tasks,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in done:
                            with suppress(asyncio.CancelledError):
                                exc = task.exception()
                                if exc:
                                    _LOGGER.warning("FCM task ended with error: %s", exc)
                        for task in pending:
                            task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    _LOGGER.warning("FCM receiver error: %s. Retrying in %.0fs", err, backoff)
                finally:
                    if client is not None:
                        with suppress(Exception):
                            await client.stop()
                        if client.tasks:
                            for task in client.tasks:
                                if not task.done():
                                    task.cancel()
                            await asyncio.gather(*client.tasks, return_exceptions=True)

                if time.monotonic() - started_at >= FCM_STABLE_SECONDS:
                    backoff = FCM_BACKOFF_INITIAL
                else:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, FCM_BACKOFF_MAX)
        except asyncio.CancelledError:
            _LOGGER.info("FCM receiver task cancelled")
            raise
