"""Only named reads; no generic API/URL, account writes or automatic password re-login."""

import hashlib
import logging
import tempfile
import time
from contextlib import nullcontext
from datetime import date, timedelta
from getpass import getpass

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from .storage import LocalError, private_file

METHODS = {
    "hrv": "get_hrv_data",
    "sleep": "get_sleep_data",
    "heart_rate": "get_heart_rates",
    "rhr": "get_rhr_day",
    "readiness": "get_training_readiness",
    "stress": "get_stress_data",
    "body_battery": "get_body_battery",
}


def error_code(exc):
    # Do not persist raw auth errors, URLs, request headers or response bodies.
    if isinstance(exc, GarminConnectAuthenticationError):
        return "auth"
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return "rate_limit"
    if isinstance(exc, GarminConnectNotFoundError):
        return "not_found"
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in (401, 403, 429, 404):
        return {401: "auth", 403: "access_denied", 429: "rate_limit", 404: "not_found"}[status]
    return "transport" if isinstance(exc, GarminConnectConnectionError) else "unexpected"


def account_identity(api):
    profile = getattr(api.client, "profile", None) or {}
    for key in ("profileId", "id", "displayName"):
        value = profile.get(key)
        if value is not None and str(value):
            return hashlib.sha256(f"garmin.cn:{key}:{value}".encode()).hexdigest()
    if api.display_name:
        return hashlib.sha256(f"garmin.cn:displayName:{api.display_name}".encode()).hexdigest()
    raise LocalError("account_identity_missing")


def authenticate(
    archive, *, interactive=False, force=False, factory=Garmin, prompt=input, secret=getpass
):
    # CLI process only: upstream debug/warning paths sometimes contain SSO tickets.
    logging.disable(logging.CRITICAL)
    token = archive.tokens / "garmin_tokens.json"
    if token.exists() or token.is_symlink():
        private_file(token)
    if not interactive and not token.exists():
        raise LocalError("login_required")
    if force and not interactive:
        raise LocalError("interactive_reauth_required")
    fresh = interactive and (force or not token.exists())
    # A new/wrong account must not replace the existing token before identity verification.
    staging = (
        tempfile.TemporaryDirectory(prefix=".login-", dir=archive.root)
        if fresh
        else nullcontext(str(archive.tokens))
    )
    with staging as login_path:
        email = password = None
        if fresh:
            email = prompt("佳明中国大陆账号（邮箱）：").strip()
            password = secret("密码（隐藏输入，不保存密码）：")

        def mfa():
            if not interactive:
                raise LocalError("interactive_reauth_required")
            return secret("佳明验证码（隐藏输入）：").strip()

        api = factory(
            email=email,
            password=password,
            is_cn=True,
            prompt_mfa=mfa,
            retry_attempts=0,
        )
        password = None
        try:
            status, _ = api.login(login_path)
            if status:
                raise LocalError("mfa_not_completed")
            archive.bind(account_identity(api))
            # Explicit dump: library login can suppress persistence failure.
            api.client.dump(str(archive.tokens))
            private_file(token)
            # Public load() also sets the refresh persistence target to the permanent directory.
            api.client.load(str(archive.tokens))
        except LocalError:
            raise
        except Exception as exc:
            raise LocalError(error_code(exc)) from None
        finally:
            api.password = None
        return api


def sync(archive, api, start, end, *, delay=1.5, sleep=time.sleep, progress=None):
    if not isinstance(start, date) or not isinstance(end, date):
        raise LocalError("invalid_date")
    count = (end - start).days + 1
    if not 1 <= count <= 180:
        raise LocalError("date_range_bound")
    if delay < 1:
        raise LocalError("minimum_request_interval")
    archive.bind(account_identity(api))
    stats = {"ok": 0, "empty": 0, "unavailable": 0, "error": 0, "requested_days": count}
    # Include the preceding calendar day's HR so midnight does not split the first night.
    requests = [(start - timedelta(days=1), "heart_rate")]
    for offset in range(count):
        day = start + timedelta(days=offset)
        requests.extend((day, kind) for kind in METHODS)
    for i, (day, kind) in enumerate(requests):
        if i:
            sleep(delay)
        stamp = day.isoformat()
        try:
            payload = getattr(api, METHODS[kind])(stamp)
        except Exception as exc:
            code = error_code(exc)
            status = "unavailable" if code == "not_found" else "error"
            archive.record(stamp, kind, None, status=status, error_code=code)
            stats[status] += 1
            if progress:
                progress(stamp, kind, status)
            if code != "not_found":
                # Preserve all completed observations, no hot-loop retry on 429/auth/5xx.
                raise LocalError(code) from None
        else:
            status = "empty" if payload is None or payload == {} or payload == [] else "ok"
            archive.record(stamp, kind, payload, status=status)
            stats[status] += 1
            if progress:
                progress(stamp, kind, status)
    return stats
