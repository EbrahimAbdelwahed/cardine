"""Small, process-local access boundary for Cardine's private shell.

This module deliberately does not know about the study domain.  It owns only
the one configured password verifier and short-lived opaque browser sessions.
The deployment can therefore opt in to private mode without adding account
tables or making the reusable harness aware of credentials.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import ipaddress
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from urllib.parse import urlsplit

PASSWORD_HASH_PREFIX = "scrypt$v1"
DEFAULT_SCRYPT_N = 16_384
DEFAULT_SCRYPT_R = 8
DEFAULT_SCRYPT_P = 1
DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60
DEFAULT_MAX_SESSIONS = 64
DEFAULT_LOGIN_WINDOW_SECONDS = 60.0
DEFAULT_LOGIN_ATTEMPTS = 5
MIN_PASSWORD_LENGTH = 12
DEV_SESSION_COOKIE = "cardine_session"
PRODUCTION_SESSION_COOKIE = "__Host-cardine_session"


class PrivateAccessError(ValueError):
    """A private access operation cannot be completed safely."""


class LoginRateLimited(PrivateAccessError):
    """The bounded login budget for one client has been exhausted."""

    retry_after_seconds = int(DEFAULT_LOGIN_WINDOW_SECONDS)


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticatedSession:
    """Opaque login result; the session token is never part of its repr."""

    session_token: str
    csrf_token: str
    expires_at: float

    def __repr__(self) -> str:  # pragma: no cover - defensive redaction
        return "AuthenticatedSession(<opaque>)"


@dataclass(slots=True)
class _SessionState:
    csrf_token: str
    expires_at: float


def hash_password(
    password: str,
    *,
    n: int = DEFAULT_SCRYPT_N,
    r: int = DEFAULT_SCRYPT_R,
    p: int = DEFAULT_SCRYPT_P,
    salt: bytes | None = None,
) -> str:
    """Create a versioned scrypt hash suitable for deployment configuration."""

    _validate_password_for_creation(password)
    _validate_scrypt_parameters(n, r, p)
    actual_salt = secrets.token_bytes(16) if salt is None else bytes(salt)
    if not 16 <= len(actual_salt) <= 64:
        raise ValueError("salt must contain between 16 and 64 bytes")
    digest = hashlib.scrypt(password.encode("utf-8"), salt=actual_salt, n=n, r=r, p=p, dklen=32)

    def encoded(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    return f"{PASSWORD_HASH_PREFIX}$N={n},r={r},p={p}${encoded(actual_salt)}${encoded(digest)}"


def generate_password_hash(password: str, **kwargs: object) -> str:
    """Compatibility spelling for callers generating deployment hashes."""

    return hash_password(password, **kwargs)  # type: ignore[arg-type]


class PrivateAccessController:
    """Verify one owner password and manage bounded in-memory sessions."""

    def __init__(
        self,
        password_hash: str,
        *,
        canonical_origin: str = "http://127.0.0.1:8765",
        production: bool = False,
        session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        login_window_seconds: float = DEFAULT_LOGIN_WINDOW_SECONDS,
        max_login_attempts: int = DEFAULT_LOGIN_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        parsed = _parse_password_hash(password_hash)
        origin = _parse_origin(canonical_origin)
        if origin is None:
            raise ValueError("canonical_origin must be an absolute origin")
        scheme, hostname, _port = origin
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname in {"localhost", "localhost.localdomain"}
        if (not loopback or production) and scheme != "https":
            raise ValueError("non-loopback private origins require HTTPS")
        if type(session_ttl_seconds) is not int or not 1 <= session_ttl_seconds <= 7 * 24 * 3600:
            raise ValueError("session_ttl_seconds is out of bounds")
        if type(max_sessions) is not int or not 1 <= max_sessions <= 1024:
            raise ValueError("max_sessions is out of bounds")
        if (
            not isinstance(login_window_seconds, (int, float))
            or not 1 <= login_window_seconds <= 3600
        ):
            raise ValueError("login_window_seconds is out of bounds")
        if type(max_login_attempts) is not int or not 1 <= max_login_attempts <= 100:
            raise ValueError("max_login_attempts is out of bounds")
        self._password_hash = password_hash
        self._parsed_hash = parsed
        self._canonical_origin = origin
        # HTTPS/private-host origins must never depend on a second flag for
        # bearer-cookie safety.  Only explicit loopback HTTP uses the
        # development cookie.
        self._production = production or scheme == "https" or not loopback
        self._session_ttl = session_ttl_seconds
        self._max_sessions = max_sessions
        self._login_window = float(login_window_seconds)
        self._max_login_attempts = max_login_attempts
        self._clock = clock
        self._lock = RLock()
        self._sessions: dict[str, _SessionState] = {}
        self._failed_logins: dict[str, list[float]] = {}

    @property
    def canonical_origin(self) -> str:
        scheme, host, port = self._canonical_origin
        suffix = "" if (scheme, port) in (("http", 80), ("https", 443)) else f":{port}"
        return f"{scheme}://{host}{suffix}"

    @property
    def production(self) -> bool:
        return self._production

    @property
    def cookie_name(self) -> str:
        return PRODUCTION_SESSION_COOKIE if self._production else DEV_SESSION_COOKIE

    @property
    def session_ttl_seconds(self) -> int:
        return self._session_ttl

    def origin_allowed(self, origin: str | None) -> bool:
        """Return whether an Origin header is exactly the configured origin."""

        return origin is not None and _parse_origin(origin) == self._canonical_origin

    def host_allowed(self, host: str | None, *, scheme: str | None = None) -> bool:
        if host is None:
            return False
        parts = _parse_origin(f"{scheme or self._canonical_origin[0]}://{host}")
        return parts == self._canonical_origin

    def login(self, password: str, *, client_id: str = "default") -> AuthenticatedSession:
        """Authenticate and issue a fresh opaque session plus CSRF token."""

        if not isinstance(client_id, str) or not client_id or len(client_id) > 128:
            client_id = "default"
        now = self._clock()
        with self._lock:
            self._prune(now)
            attempts = [
                stamp
                for stamp in self._failed_logins.get(client_id, ())
                if now - stamp < self._login_window
            ]
            self._failed_logins[client_id] = attempts
            if len(attempts) >= self._max_login_attempts:
                raise LoginRateLimited("login temporarily unavailable")
            valid = self._verify(password)
            if not valid:
                attempts.append(now)
                self._failed_logins[client_id] = attempts
                raise PrivateAccessError("invalid credentials")
            self._failed_logins.pop(client_id, None)
            while len(self._sessions) >= self._max_sessions:
                oldest = min(self._sessions, key=lambda token: self._sessions[token].expires_at)
                self._sessions.pop(oldest, None)
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            expires_at = now + self._session_ttl
            self._sessions[token] = _SessionState(csrf, expires_at)
            return AuthenticatedSession(token, csrf, expires_at)

    def verify_password(self, password: str) -> bool:
        """Check the configured owner password without creating a session."""

        with self._lock:
            return self._verify(password)

    def authenticate(self, session_token: str | None) -> bool:
        """Validate a cookie token without exposing internal session state."""

        return self.session(session_token) is not None

    def session(self, session_token: str | None) -> AuthenticatedSession | None:
        if not isinstance(session_token, str) or not session_token or len(session_token) > 256:
            return None
        now = self._clock()
        with self._lock:
            self._prune(now)
            state = self._sessions.get(session_token)
            if state is None:
                return None
            return AuthenticatedSession(session_token, state.csrf_token, state.expires_at)

    def csrf_valid(self, session_token: str | None, csrf_token: str | None) -> bool:
        session = self.session(session_token)
        return (
            session is not None
            and isinstance(csrf_token, str)
            and hmac.compare_digest(session.csrf_token, csrf_token)
        )

    def logout(self, session_token: str | None) -> None:
        if not isinstance(session_token, str):
            return
        with self._lock:
            self._sessions.pop(session_token, None)

    def cookie_header(self, session_token: str, *, max_age: int | None = None) -> str:
        """Return a Set-Cookie value with the ADR-0019 attributes."""

        if not isinstance(session_token, str) or not session_token:
            raise ValueError("session_token must be non-empty")
        age = self._session_ttl if max_age is None else max_age
        value = (
            f"{self.cookie_name}={session_token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}"
        )
        if self._production:
            value += "; Secure"
        return value

    def clear_cookie_header(self) -> str:
        value = f"{self.cookie_name}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        if self._production:
            value += "; Secure"
        return value

    def _verify(self, password: str) -> bool:
        try:
            _validate_password_for_verification(password)
            salt, expected, n, r, p = self._parsed_hash
            actual = hashlib.scrypt(
                password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
            )
            return hmac.compare_digest(actual, expected)
        except (TypeError, ValueError, UnicodeError):
            return False

    def _prune(self, now: float) -> None:
        self._sessions = {
            token: state for token, state in self._sessions.items() if state.expires_at > now
        }
        self._failed_logins = {
            key: [stamp for stamp in stamps if now - stamp < self._login_window]
            for key, stamps in self._failed_logins.items()
            if any(now - stamp < self._login_window for stamp in stamps)
        }


def _validate_password_for_creation(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH or len(password) > 4096:
        raise ValueError(
            f"password must be non-empty, contain at least {MIN_PASSWORD_LENGTH} "
            "characters, and be bounded"
        )


def _validate_password_for_verification(password: str) -> None:
    """Keep accepting hashes made before the creation minimum was introduced."""

    if not isinstance(password, str) or not password or len(password) > 4096:
        raise ValueError("password must be non-empty and bounded")


def _validate_scrypt_parameters(n: int, r: int, p: int) -> None:
    if type(n) is not int or n < 2 or n & (n - 1) or n > 2**20:
        raise ValueError("invalid scrypt cost")
    if type(r) is not int or not 1 <= r <= 64 or type(p) is not int or not 1 <= p <= 32:
        raise ValueError("invalid scrypt parameters")


def _decode(value: str) -> bytes:
    if not value or len(value) > 256:
        raise ValueError("invalid encoded value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _parse_password_hash(value: str) -> tuple[bytes, bytes, int, int, int]:
    if not isinstance(value, str) or len(value) > 1024:
        raise ValueError("password hash is invalid")
    fields = value.split("$")
    if len(fields) != 5 or fields[:2] != ["scrypt", "v1"]:
        raise ValueError("password hash must use scrypt$v1")
    params: dict[str, int] = {}
    for part in fields[2].split(","):
        key, separator, raw = part.partition("=")
        if not separator or key not in {"N", "r", "p"} or not raw.isdigit():
            raise ValueError("password hash parameters are invalid")
        params[key] = int(raw)
    if set(params) != {"N", "r", "p"}:
        raise ValueError("password hash parameters are invalid")
    _validate_scrypt_parameters(params["N"], params["r"], params["p"])
    salt = _decode(fields[3])
    digest = _decode(fields[4])
    if not 16 <= len(salt) <= 64 or len(digest) < 16 or len(digest) > 64:
        raise ValueError("password hash payload is invalid")
    return salt, digest, params["N"], params["r"], params["p"]


def _parse_origin(value: str) -> tuple[str, str, int] | None:
    if not isinstance(value, str) or len(value) > 512:
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), hostname.lower(), port if port is not None else default_port


def main(argv: list[str] | None = None) -> int:
    """Generate a hash without accepting the password as a shell argument."""

    parser = argparse.ArgumentParser(prog="cardine-private-password-hash")
    parser.add_argument("--confirm", action="store_true", help="request the password twice")
    args = parser.parse_args(argv)
    first = getpass.getpass("Password: ")
    if args.confirm:
        second = getpass.getpass("Password again: ")
        if not hmac.compare_digest(first, second):
            print("passwords do not match", file=sys.stderr)
            return 2
    try:
        print(hash_password(first))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


__all__ = [
    "DEV_SESSION_COOKIE",
    "MIN_PASSWORD_LENGTH",
    "PRODUCTION_SESSION_COOKIE",
    "AuthenticatedSession",
    "LoginRateLimited",
    "PrivateAccessController",
    "PrivateAccessError",
    "generate_password_hash",
    "hash_password",
    "main",
]
