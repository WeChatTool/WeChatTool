"""Exact, reviewed accessibility hooks; no offset matching across versions."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

from .macho import MachO

ROLES = ("interface", "actions")
PROFILE_FIELDS = {"id", "arch", "version", "build", "uuid", "image_sha256", "address", "expected",
                  "patch_offset", "replacement", "function_size", "function_sha256"}


def profile_id(role: str, arch: str) -> str:
    return f"accessibility-{role}-{arch}-v1"


def validate_profile(rule: dict) -> None:
    try:
        if not isinstance(rule, dict) or set(rule) != PROFILE_FIELDS:
            raise ValueError("missing or unknown profile fields")
        if rule["arch"] not in ("arm64", "x86_64"):
            raise ValueError("unsupported architecture")
        if rule["id"] not in [profile_id(role, rule["arch"]) for role in ROLES]:
            raise ValueError("unsupported hook ID")
        for key in ("version", "build"):
            if (not isinstance(rule[key], str) or not 1 <= len(rule[key]) <= 64 or
                    any(ord(character) < 32 or ord(character) == 127 for character in rule[key])):
                raise ValueError(f"missing {key}")
        if not isinstance(rule["uuid"], str) or str(uuid.UUID(rule["uuid"])) != rule["uuid"]:
            raise ValueError("invalid UUID")
        for key in ("image_sha256", "function_sha256"):
            if not isinstance(rule[key], str) or not re.fullmatch(r"[0-9a-f]{64}", rule[key]):
                raise ValueError(f"invalid {key}")
        for key in ("address", "patch_offset", "function_size"):
            if type(rule[key]) is not int or not 0 <= rule[key] <= (1 << 64) - 1:
                raise ValueError(f"invalid {key}")
        if (not rule["address"] or not 0 < rule["function_size"] <= 65536 or
                rule["address"] + rule["function_size"] > (1 << 64) - 1):
            raise ValueError("invalid function range")
        for key in ("expected", "replacement"):
            if not isinstance(rule[key], str) or not re.fullmatch(r"(?:[0-9a-f]{2})+", rule[key]):
                raise ValueError(f"invalid {key}")
        expected, replacement = bytes.fromhex(rule["expected"]), bytes.fromhex(rule["replacement"])
        if (not expected or not replacement or len(expected) > 2048 or len(replacement) > 16 or
                len(expected) > rule["function_size"] or
                rule["patch_offset"] + len(replacement) > len(expected)):
            raise ValueError("patch does not fit its verified function prefix")
        if rule["arch"] == "arm64" and any(value % 4 for value in (
                rule["address"], rule["patch_offset"], len(replacement), rule["function_size"])):
            raise ValueError("unaligned arm64 hook")
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError(f"Invalid accessibility profile: {error}") from error


def validate_document(document: dict) -> dict:
    if (not isinstance(document, dict) or set(document) != {"schema_version", "profiles"} or
            type(document.get("schema_version")) is not int or document["schema_version"] != 1 or
            not isinstance(document.get("profiles"), list)):
        raise ValueError("Unsupported accessibility profile schema.")
    ids, identities = set(), set()
    for rule in document["profiles"]:
        validate_profile(rule)
        identity = (rule["arch"], rule["version"], rule["build"], rule["uuid"],
                    rule["image_sha256"], rule["address"])
        if rule["id"] in ids or identity in identities:
            raise ValueError("Duplicate accessibility profile ID or function identity.")
        ids.add(rule["id"])
        identities.add(identity)
    return document


def profiles() -> list[dict]:
    document = json.loads(Path(__file__).with_name("accessibility_profiles.json").read_text())
    return validate_document(document)["profiles"]


def scan_accessibility(data: bytes, rules: list[dict], version: str, build: str,
                       digest: str | None = None) -> tuple[list[dict], list[dict]]:
    """Verify each complete function and require both hooks for each image slice."""
    for rule in rules:
        validate_profile(rule)
    image = MachO(data)
    digest = digest or hashlib.sha256(data).hexdigest()
    hooks, diagnostics = [], []
    for part in image.slices:
        candidates = []
        for rule in rules:
            if (rule["arch"] != part.arch or rule["version"] != version or rule["build"] != build
                    or rule["uuid"] != part.uuid or rule["image_sha256"] != digest):
                continue
            address, size = rule["address"], rule["function_size"]
            if address not in part.function_starts:
                continue
            sections = [section for section in part.sections
                        if section.name == "__text" and section.segment == "__TEXT"
                        and section.file_backed and section.flags & 0x80000000
                        and section.addr <= address and address + size <= section.addr + section.size
                        and any(segment.name == "__TEXT" and segment.initprot & 5 == 5
                                and segment.addr <= section.addr
                                and section.addr + section.size <= segment.addr + segment.size
                                for segment in part.segments)]
            if len(sections) != 1:
                continue
            section = sections[0]
            end = min((start for start in part.function_starts if start > address),
                      default=section.addr + section.size)
            if address + size != end:
                continue
            offset = part.vm_to_offset(address, size)
            body = data[offset:offset + size]
            if (hashlib.sha256(body).hexdigest() != rule["function_sha256"] or
                    not body.startswith(bytes.fromhex(rule["expected"]))):
                continue
            candidates.append({key: rule[key] for key in (
                "id", "arch", "uuid", "address", "expected", "patch_offset", "replacement",
                "function_size", "function_sha256")})
        counts = {role: sum(hook["id"] == profile_id(role, part.arch) for hook in candidates)
                  for role in ROLES}
        status = ("ambiguous" if any(count > 1 for count in counts.values()) else
                  "matched" if all(count == 1 for count in counts.values()) else "unsupported")
        diagnostics.append({"arch": part.arch, "status": status, "matches": len(candidates),
                            "required_hooks": len(ROLES)})
        if status == "matched":
            hooks.extend(candidates)
    return hooks, diagnostics
