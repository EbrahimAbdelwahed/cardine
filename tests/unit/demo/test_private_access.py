from __future__ import annotations

import base64
import hashlib
import threading
from collections.abc import Callable
from typing import cast

import pytest

from study_agent.demo.private_access import (
    DEFAULT_SESSION_TTL_SECONDS,
    MIN_PASSWORD_LENGTH,
    LoginRateLimited,
    PrivateAccessController,
    PrivateAccessError,
    hash_password,
    main,
)

PASSWORD = "correct horse battery staple"
SALT = b"0123456789abcdef"


def _controller(
    *,
    canonical_origin: str = "http://127.0.0.1:8765",
    production: bool = False,
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    max_sessions: int = 16,
    login_window_seconds: float = 60.0,
    max_login_attempts: int = 5,
    clock: Callable[[], float] | None = None,
) -> PrivateAccessController:
    return PrivateAccessController(
        hash_password(PASSWORD, n=2, r=1, p=1, salt=SALT),
        canonical_origin=canonical_origin,
        production=production,
        session_ttl_seconds=session_ttl_seconds,
        max_sessions=max_sessions,
        login_window_seconds=login_window_seconds,
        max_login_attempts=max_login_attempts,
        **({} if clock is None else {"clock": clock}),
    )


def test_hash_is_versioned_and_verifies_with_bounded_scrypt_parameters() -> None:
    encoded = hash_password(PASSWORD, n=2, r=1, p=1, salt=SALT)

    assert encoded.startswith("scrypt$v1$N=2,r=1,p=1$")
    access = PrivateAccessController(encoded, canonical_origin="http://127.0.0.1:8765")
    assert access.verify_password(PASSWORD)
    assert not access.verify_password("wrong password")
    assert not access.verify_password("x" * 4_097)


@pytest.mark.parametrize("password", ("x" * (MIN_PASSWORD_LENGTH - 1), ""))
def test_password_creation_requires_the_minimum_length(password: str) -> None:
    with pytest.raises(ValueError, match="at least"):
        hash_password(password, n=2, r=1, p=1, salt=SALT)


def test_verification_keeps_compatibility_with_a_legacy_short_password_hash() -> None:
    legacy_password = "legacy"
    digest = hashlib.scrypt(legacy_password.encode("utf-8"), salt=SALT, n=2, r=1, p=1, dklen=32)
    encoded = "$".join(
        (
            "scrypt",
            "v1",
            "N=2,r=1,p=1",
            base64.urlsafe_b64encode(SALT).rstrip(b"=").decode("ascii"),
            base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii"),
        )
    )

    access = PrivateAccessController(encoded)
    assert access.verify_password(legacy_password)


def test_password_hash_cli_rejects_short_creation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "study_agent.demo.private_access.getpass.getpass", lambda _prompt: "too-short"
    )

    assert main([]) == 2
    assert "at least" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"n": 3}, "invalid scrypt cost"),
        ({"n": 2**21}, "invalid scrypt cost"),
        ({"r": 0}, "invalid scrypt parameters"),
        ({"p": 33}, "invalid scrypt parameters"),
        ({"salt": b"short"}, "salt must contain"),
    ),
)
def test_hash_rejects_unbounded_parameters(kwargs: dict[str, object], message: str) -> None:
    options: dict[str, object] = {"n": 2, "r": 1, "p": 1, "salt": SALT}
    options.update(kwargs)
    with pytest.raises(ValueError, match=message):
        hash_password(
            PASSWORD,
            n=cast(int, options["n"]),
            r=cast(int, options["r"]),
            p=cast(int, options["p"]),
            salt=cast(bytes, options["salt"]),
        )


@pytest.mark.parametrize(
    "value",
    (
        "",
        "scrypt$v2$N=2,r=1,p=1$AA$AA",
        "scrypt$v1$N=3,r=1,p=1$AA$AA",
        "scrypt$v1$N=2,r=1$AA$AA",
        "scrypt$v1$N=2,r=1,p=1$not-base64!$AA",
        "scrypt$v1$N=2,r=1,p=1$AA$AA",
    ),
)
def test_controller_rejects_invalid_password_hash_formats(value: str) -> None:
    with pytest.raises(ValueError):
        PrivateAccessController(value)


def test_password_and_hash_payload_bounds_fail_closed() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        hash_password("")
    with pytest.raises(ValueError, match="bounded"):
        hash_password("x" * 4_097)

    valid = hash_password(PASSWORD, n=2, r=1, p=1, salt=SALT)
    salt, digest = valid.rsplit("$", 2)[-2:]
    too_short = base64.urlsafe_b64encode(b"short").rstrip(b"=").decode()
    malformed = valid.replace(salt, too_short, 1)
    with pytest.raises(ValueError, match="payload"):
        PrivateAccessController(malformed)
    assert digest


def test_login_rate_limit_resets_and_session_expiry_is_enforced() -> None:
    now = [100.0]
    access = _controller(
        clock=lambda: now[0],
        login_window_seconds=10,
        max_login_attempts=2,
        session_ttl_seconds=5,
        max_sessions=2,
    )

    for _ in range(2):
        with pytest.raises(PrivateAccessError, match="invalid credentials"):
            access.login("wrong", client_id="client")
    with pytest.raises(LoginRateLimited):
        access.login(PASSWORD, client_id="client")

    now[0] += 10
    session = access.login(PASSWORD, client_id="client")
    assert access.authenticate(session.session_token)
    assert access.csrf_valid(session.session_token, session.csrf_token)
    assert not access.csrf_valid(session.session_token, "wrong-csrf")

    now[0] = session.expires_at
    assert not access.authenticate(session.session_token)
    assert access.session(session.session_token) is None


def test_sessions_are_bounded_and_oldest_expiry_is_evicted() -> None:
    now = [100.0]
    access = _controller(clock=lambda: now[0], max_sessions=1)
    first = access.login(PASSWORD)
    now[0] += 1
    second = access.login(PASSWORD)

    assert not access.authenticate(first.session_token)
    assert access.authenticate(second.session_token)


def test_origin_host_csrf_logout_and_cookie_attributes_are_canonical() -> None:
    access = _controller(canonical_origin="http://127.0.0.1:8765")
    assert access.canonical_origin == "http://127.0.0.1:8765"
    assert access.origin_allowed("http://127.0.0.1:8765")
    assert not access.origin_allowed("http://127.0.0.1:8766")
    assert access.host_allowed("127.0.0.1:8765")
    assert not access.host_allowed("127.0.0.1:8765", scheme="https")
    assert not access.host_allowed("evil.example")

    session = access.login(PASSWORD)
    assert access.cookie_header(session.session_token) == (
        f"cardine_session={session.session_token}; HttpOnly; SameSite=Strict; "
        f"Path=/; Max-Age={DEFAULT_SESSION_TTL_SECONDS}"
    )
    assert access.clear_cookie_header() == (
        "cardine_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
    )
    assert repr(session) == "AuthenticatedSession(<opaque>)"
    assert session.session_token not in repr(session)
    assert session.csrf_token not in repr(session)

    access.logout(session.session_token)
    assert not access.authenticate(session.session_token)


def test_production_cookies_and_origins_require_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        _controller(production=True, canonical_origin="http://127.0.0.1:8765")
    access = _controller(production=True, canonical_origin="https://private.example")
    session = access.login(PASSWORD)
    assert access.cookie_name == "__Host-cardine_session"
    assert access.cookie_header(session.session_token).endswith("; Secure")
    assert access.clear_cookie_header().endswith("; Secure")
    assert access.origin_allowed("https://private.example")
    assert not access.origin_allowed("http://private.example")


def test_https_origin_derives_secure_host_cookie_without_a_second_flag() -> None:
    access = _controller(
        production=False,
        canonical_origin="https://private.example",
    )
    session = access.login(PASSWORD)

    assert access.production
    assert access.cookie_name == "__Host-cardine_session"
    assert access.cookie_header(session.session_token).endswith("; Secure")


def test_login_and_session_calls_are_safe_under_concurrent_access() -> None:
    access = _controller(max_sessions=32)
    sessions: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            value = access.login(PASSWORD)
            sessions.append(value.session_token)
            assert access.csrf_valid(value.session_token, value.csrf_token)
            assert access.authenticate(value.session_token)
        except BaseException as error:  # pragma: no cover - only reports a race
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(sessions) == 8
