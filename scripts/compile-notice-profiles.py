#!/usr/bin/env python3
"""Embed the reviewed record layouts in the native plugin at build time."""
import json
import re
import sys
from pathlib import Path
import uuid


def validate_document(document: object) -> dict:
    """Reject bad reviewed-source profiles before they can enter a native build."""
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    def integer(value: object, maximum: int = (1 << 64) - 1) -> bool:
        return type(value) is int and 0 < value <= maximum

    def string(value: object, maximum: int) -> bool:
        return isinstance(value, str) and 0 < len(value) <= maximum and all(ord(c) >= 32 and ord(c) != 127 for c in value)

    def digest(value: object) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    require(isinstance(document, dict) and set(document) == {"schema_version", "adapters"}, "Invalid notice document fields")
    require(type(document["schema_version"]) is int and document["schema_version"] == 1,
            "Unsupported notice adapter schema")
    require(isinstance(document["adapters"], list), "adapters must be an array")
    identifiers, identities = set(), set()
    layouts = {"arm64": "messagewrap-libcpp-alt-0x130", "x86_64": "messagewrap-libcpp-default-0x130"}
    fields = {"id", "layout", "version", "build", "arch", "uuid", "image_sha256", "predicate_address",
              "insert_notice", "handler", "task_slot"}
    for index, profile in enumerate(document["adapters"]):
        prefix = f"adapters[{index}]"
        require(isinstance(profile, dict) and set(profile) == fields, f"{prefix}: invalid adapter fields")
        require(string(profile["id"], 128) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", profile["id"]) is not None,
                f"{prefix}: invalid adapter id")
        require(string(profile["version"], 64) and string(profile["build"], 64), f"{prefix}: invalid version/build")
        require(isinstance(profile["arch"], str) and profile["arch"] in layouts, f"{prefix}: unsupported architecture")
        require(profile["layout"] == layouts[profile["arch"]], f"{prefix}: unsupported architecture/layout pair")
        try:
            canonical_uuid = str(uuid.UUID(profile["uuid"])) if isinstance(profile["uuid"], str) else None
        except ValueError:
            canonical_uuid = None
        require(canonical_uuid is not None and profile["uuid"] == canonical_uuid, f"{prefix}: UUID must be canonical lowercase")
        require(digest(profile["image_sha256"]), f"{prefix}: invalid image SHA256")
        address = profile["predicate_address"]
        require(integer(address) and address <= (1 << 64) - 21, f"{prefix}: invalid predicate address")
        require(profile["arch"] != "arm64" or address % 4 == 0, f"{prefix}: unaligned ARM predicate")
        for key in ("insert_notice", "handler", "task_slot"):
            probe = profile[key]
            probe_fields = {"address", "size", "sha256"} | ({"expected"} if key == "handler" else set())
            require(isinstance(probe, dict) and set(probe) == probe_fields, f"{prefix}: invalid {key} fields")
            require(integer(probe["address"]) and integer(probe["size"], 65536), f"{prefix}: invalid {key} range")
            require(probe["address"] <= (1 << 64) - 1 - probe["size"], f"{prefix}: overflowing {key} range")
            require(profile["arch"] != "arm64" or probe["address"] % 4 == 0, f"{prefix}: unaligned ARM {key}")
            require(digest(probe["sha256"]), f"{prefix}: invalid {key} SHA256")
            if key == "handler":
                require(probe["size"] >= 32 and digest(probe["expected"]), f"{prefix}: invalid handler instructions")
        identity = tuple(profile[key] for key in ("version", "build", "arch", "uuid", "image_sha256", "predicate_address"))
        require(profile["id"] not in identifiers, f"{prefix}: duplicate adapter id")
        require(identity not in identities, f"{prefix}: ambiguous adapter identity")
        identifiers.add(profile["id"])
        identities.add(identity)
    return document


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    document = validate_document(json.loads((root / "wechattool/notice_profiles.json").read_text()))
    payload = json.dumps(document, ensure_ascii=True, separators=(",", ":"))
    Path(sys.argv[1]).write_text(
        "// Generated from reviewed source profiles; never read from an app plan.\n"
        "static const char kCompiledNoticeProfiles[] = " + json.dumps(payload) + ";\n"
    )


if __name__ == "__main__":
    main()
