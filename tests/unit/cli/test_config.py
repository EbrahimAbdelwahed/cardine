from __future__ import annotations

from dataclasses import fields

import pytest

from cardine.cli.config import (
    EMPTY_CONFIG,
    LocalConfigError,
    LocalRepositoryConfig,
    ModelAdapterConfig,
)
from study_agent.domain.features import FeatureMode
from study_agent.repository_config import (
    CONFIG_SCHEMA_VERSION,
    DocumentIndexAdapterConfig,
    JudgementAdapterConfig,
    SemanticFeaturesConfig,
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


def test_schema_one_migrates_explicitly_to_off_without_persisting_credentials() -> None:
    old = b'{"model":null,"schema_version":1}'
    current = LocalRepositoryConfig.from_bytes(old)
    assert current.schema_version == CONFIG_SCHEMA_VERSION == 2
    assert current.features == SemanticFeaturesConfig()
    assert current.judgement is None
    assert current.to_bytes() != old
    assert LocalRepositoryConfig.from_bytes(current.to_bytes()) == current


def test_independent_provider_and_feature_policy_configuration_round_trips() -> None:
    features = SemanticFeaturesConfig(
        document_index_mode=FeatureMode.ON,
        flashcard_semantic_mode=FeatureMode.ON,
        tutor_routing_mode=FeatureMode.SHADOW,
        exclusion_probability=0.995,
        emergency_fallback=False,
    )
    config = LocalRepositoryConfig(
        configured().model,
        judgement=JudgementAdapterConfig(concurrency=32),
        document_index=DocumentIndexAdapterConfig(timeout_seconds=5),
        features=features,
    )
    assert LocalRepositoryConfig.from_bytes(config.to_bytes()) == config
    assert b"OPENROUTER_API_KEY" in config.to_bytes()
    assert b"resolved_model_id" in config.to_bytes()


@pytest.mark.parametrize(
    "field,value",
    [
        ("concurrency", True),
        ("concurrency", 0),
        ("max_retries", -1),
        ("timeout_seconds", float("nan")),
        ("credential_env", "literal-token"),
        ("credential_env", 3),
        ("model_id", None),
        ("resolved_model_id", " "),
    ],
)
def test_judgement_config_rejects_invalid_operational_values(field: str, value: object) -> None:
    with pytest.raises(LocalConfigError):
        JudgementAdapterConfig(**{field: value})  # type: ignore[arg-type]


def test_enabled_features_require_explicit_providers_and_valid_mode_dependencies() -> None:
    with pytest.raises(LocalConfigError, match="judgement"):
        LocalRepositoryConfig(features=SemanticFeaturesConfig(tutor_routing_mode=FeatureMode.ON))
    with pytest.raises(LocalConfigError, match="document indexing"):
        SemanticFeaturesConfig(flashcard_semantic_mode=FeatureMode.ON)
    with pytest.raises(LocalConfigError, match="primary document"):
        SemanticFeaturesConfig(
            document_index_mode=FeatureMode.SHADOW, flashcard_semantic_mode=FeatureMode.ON
        )


def test_current_schema_rejects_unknown_feature_fields_and_literal_keys() -> None:
    import json

    raw = json.loads(EMPTY_CONFIG.to_bytes())
    raw["features"]["surprise"] = True
    with pytest.raises(LocalConfigError):
        LocalRepositoryConfig.from_bytes(json.dumps(raw).encode())
    raw = json.loads(EMPTY_CONFIG.to_bytes())
    raw["judgement"] = {**JudgementAdapterConfig().to_json(), "api_key": "never-persist"}
    with pytest.raises(LocalConfigError):
        LocalRepositoryConfig.from_bytes(json.dumps(raw).encode())
