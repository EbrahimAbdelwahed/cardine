from __future__ import annotations

from dataclasses import fields

import pytest

from cardine.cli.config import (
    EMPTY_CONFIG,
    LocalConfigError,
    LocalRepositoryConfig,
    ModelAdapterConfig,
)
from study_agent.repository_config import LocalRepositoryConfig as CoreRepositoryConfig


def configured() -> LocalRepositoryConfig:
    return LocalRepositoryConfig(
        ModelAdapterConfig(
            "openai-compatible-http",
            {
                "endpoint_url": "https://models.example.test/v1/chat/completions",
                "model_id": "inexpensive-prototype",
                "timeout_seconds": 30,
            },
            "STUDY_AGENT_MODEL_KEY",
        )
    )


def test_config_round_trips_canonically_without_a_secret_value() -> None:
    config = configured()
    payload = config.to_bytes()

    assert LocalRepositoryConfig.from_bytes(payload) == config
    assert LocalRepositoryConfig.from_bytes(payload).to_bytes() == payload
    assert b"STUDY_AGENT_MODEL_KEY" in payload
    assert b"credential-value" not in payload
    assert "credential-value" not in repr(config)
    assert {item.name for item in fields(ModelAdapterConfig)} == {
        "adapter_id",
        "settings",
        "credential_env",
    }


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b'{"model":null,"schema_version":true}',
        b'{"model":null,"schema_version":2}',
        b'{"extra":null,"model":null,"schema_version":1}',
        b'{"model":null,"model":null,"schema_version":1}',
        b'{"model":null,"schema_version":NaN}',
        b'{"model":{"adapter_id":"x","credential_env":null,"settings":{},'
        b'"extra":1},"schema_version":1}',
        b"\xff",
    ],
)
def test_decoder_rejects_unknown_fields_types_versions_and_invalid_utf8(
    payload: bytes,
) -> None:
    with pytest.raises(LocalConfigError):
        LocalRepositoryConfig.from_bytes(payload)


@pytest.mark.parametrize(
    "settings",
    [
        {"api_key": "must-not-persist"},
        {"nested": {"access_token": "must-not-persist"}},
        {"nested": [{"authorization": "must-not-persist"}]},
        {"accessToken": "must-not-persist"},
        {"clientSecret": "must-not-persist"},
        {"private_key": "must-not-persist"},
        {"credentials": "must-not-persist"},
        {"password": "must-not-persist"},
    ],
)
def test_settings_reject_credential_shaped_fields(settings: dict[str, object]) -> None:
    with pytest.raises(LocalConfigError, match="credential fields"):
        ModelAdapterConfig("adapter", settings)  # type: ignore[arg-type]


def test_empty_config_is_an_offline_valid_repository_configuration() -> None:
    assert LocalRepositoryConfig.from_bytes(EMPTY_CONFIG.to_bytes()) == EMPTY_CONFIG


def test_direct_configuration_rejects_non_finite_json_numbers() -> None:
    with pytest.raises(LocalConfigError, match="strict JSON"):
        ModelAdapterConfig("adapter", {"timeout_seconds": float("nan")})


def test_settings_are_excluded_from_repr_defense_in_depth() -> None:
    config = ModelAdapterConfig("adapter", {"endpoint_url": "sensitive-operational-value"})

    assert "sensitive-operational-value" not in repr(config)


def test_decoder_rejects_credential_shaped_settings() -> None:
    payload = (
        b'{"model":{"adapter_id":"adapter","credential_env":null,'
        b'"settings":{"clientSecret":"must-not-persist"}},"schema_version":1}'
    )

    with pytest.raises(LocalConfigError, match="credential fields"):
        LocalRepositoryConfig.from_bytes(payload)


def test_configuration_is_deeply_immutable() -> None:
    mutable: dict[str, object] = {"nested": {"mode": "strict"}}
    config = ModelAdapterConfig("adapter", mutable)  # type: ignore[arg-type]
    mutable["nested"] = {"mode": "changed"}

    assert config.settings["nested"] == {"mode": "strict"}
    with pytest.raises(TypeError):
        config.settings["new"] = "value"  # type: ignore[index]


def test_cli_config_is_an_identity_preserving_facade_for_the_neutral_owner() -> None:
    assert LocalRepositoryConfig is CoreRepositoryConfig


def test_configuration_serialization_rejects_more_than_64_kib() -> None:
    config = LocalRepositoryConfig(
        ModelAdapterConfig(
            "adapter",
            {f"setting_{index}": "x" * 4096 for index in range(16)},
        )
    )

    with pytest.raises(LocalConfigError, match="64 KiB"):
        config.to_bytes()
