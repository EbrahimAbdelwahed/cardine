"""Reference command-line composition layer."""

from study_agent.repository_config import (
    CONFIG_FILENAME,
    CONFIG_SCHEMA_VERSION,
    EMPTY_CONFIG,
    LocalConfigError,
    LocalRepositoryConfig,
    ModelAdapterConfig,
)

from .repository import (
    CourseRepository,
    LocalRepository,
    LocalRepositoryError,
    LocalRepositoryPaths,
    ModelAdapterBuilder,
    ModelAdapterConfigurationError,
    ModelAdapterRegistry,
    default_model_adapters,
    initialize_local_repository,
)

__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_SCHEMA_VERSION",
    "EMPTY_CONFIG",
    "CourseRepository",
    "LocalConfigError",
    "LocalRepository",
    "LocalRepositoryConfig",
    "LocalRepositoryError",
    "LocalRepositoryPaths",
    "ModelAdapterBuilder",
    "ModelAdapterConfig",
    "ModelAdapterConfigurationError",
    "ModelAdapterRegistry",
    "default_model_adapters",
    "initialize_local_repository",
]
