"""Recall metadata layouts are reviewed independently of preservation recipes."""
from __future__ import annotations

import json
from pathlib import Path


def notice_profiles() -> list[dict]:
    document = json.loads(Path(__file__).with_name("notice_profiles.json").read_text())
    if document["schema_version"] != 1:
        raise ValueError("Unsupported notice adapter schema")
    return document["adapters"]


def notice_adapter(hook: dict, version: str, build: str) -> str | None:
    matches = [profile for profile in notice_profiles()
               if profile["layout"] == {"arm64": "messagewrap-libcpp-alt-0x130",
                                        "x86_64": "messagewrap-libcpp-default-0x130"}.get(hook["arch"])
               and profile["version"] == version and profile["build"] == build
               and profile["arch"] == hook["arch"] and profile["uuid"] == hook["uuid"]
               and profile["predicate_address"] == hook["address"]
               and profile["image_sha256"] == hook["image_sha256"]]
    return matches[0]["id"] if len(matches) == 1 else None
