"""Explicit rollout modes for derived features; OFF preserves current behavior."""

from enum import StrEnum


class FeatureMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ON = "on"
