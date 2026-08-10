"""Compatibility imports for repository configuration owned by the core package."""

from study_agent.repository_config import (
    CONFIG_FILENAME,
    CONFIG_SCHEMA_VERSION,
    EMPTY_CONFIG,
    MAX_CONFIG_BYTES,
    LocalConfigError,
    LocalRepositoryConfig,
    ModelAdapterConfig,
)

__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_SCHEMA_VERSION",
    "EMPTY_CONFIG",
    "MAX_CONFIG_BYTES",
    "LocalConfigError",
    "LocalRepositoryConfig",
    "ModelAdapterConfig",
]
