"""Shared feature names and deterministic selection for installer plans."""

from __future__ import annotations

FEATURES = ("recall", "accessibility")


def selected_features(features: list[str] | tuple[str, ...] | None = None) -> list[str]:
    if features is None:
        return ["recall"]
    if not isinstance(features, (list, tuple)) or not features:
        raise ValueError("Select at least one feature: recall or accessibility.")
    if any(not isinstance(feature, str) or feature not in FEATURES for feature in features):
        raise ValueError("Unknown feature; choose recall or accessibility.")
    if len(features) != len(set(features)):
        raise ValueError("Features must not be repeated.")
    return [feature for feature in FEATURES if feature in features]
