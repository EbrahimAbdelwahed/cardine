"""Compare durable command receipts without process-local activity observations."""

from __future__ import annotations

from collections.abc import Mapping


def without_transient_activity(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: without_transient_activity(item)
            for key, item in value.items()
            if key not in {"activity_records", "activity_state"}
        }
    if isinstance(value, (tuple, list)):
        return tuple(without_transient_activity(item) for item in value)
    return value
