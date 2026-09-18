"""Find supported predicates by code and function boundaries, never version offsets."""

from __future__ import annotations

import hashlib
import json
import plistlib
from pathlib import Path

from .accessibility import profiles as accessibility_profiles, scan_accessibility
from .features import selected_features
from .macho import MachO, MachOError
from .notices import notice_adapter


class CompatibilityError(ValueError):
    pass


def confined_path(app: Path, relative: str) -> Path:
    """Resolve existing bundle paths without accepting traversal or escaping symlinks."""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise CompatibilityError(f"Invalid bundle-relative path: {relative}")
    root = app.resolve(strict=True)
    target = (root / path).resolve(strict=True)
    if not target.is_relative_to(root):
        raise CompatibilityError(f"Path escapes app bundle: {relative}")
    return target


def app_info(app: Path) -> dict:
    app = app.resolve(strict=True)
    with confined_path(app, "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if not isinstance(info, dict):
        raise CompatibilityError("Info.plist must contain a dictionary.")
    if info.get("CFBundleIdentifier") != "com.tencent.xinWeChat":
        raise CompatibilityError("Expected the main macOS WeChat bundle (com.tencent.xinWeChat).")
    if info.get("CFBundleExecutable") != "WeChat":
        raise CompatibilityError("Unsupported main executable; expected WeChat.")
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if not isinstance(info.get(key), str) or not info[key]:
            raise CompatibilityError(f"Missing {key} in Info.plist.")
    return info


def profiles() -> list[dict]:
    document = json.loads(Path(__file__).with_name("profiles.json").read_text())
    if document["schema_version"] != 1:
        raise CompatibilityError("Unsupported profile schema.")
    result = document["profiles"]
    for rule in result:
        expected, replacement = bytes.fromhex(rule["expected"]), bytes.fromhex(rule["replacement"])
        offset = rule["patch_offset"]
        if not expected or not replacement or offset < 0 or offset + len(replacement) > len(expected):
            raise CompatibilityError(f"Invalid profile: {rule['id']}")
    return result


def scan_image(data: bytes, rules: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return only unambiguous, function-start-aligned matches in executable __text."""
    image = MachO(data)
    hooks, diagnostics = [], []
    for part in image.slices:
        candidates = []
        rejected = 0
        for section in part.sections:
            if section.name != "__text" or section.segment != "__TEXT":
                continue
            # Match the runtime's exact code-region requirements.
            if not section.file_backed or not section.flags & 0x80000000:
                continue
            if not any(segment.name == "__TEXT" and segment.initprot & 5 == 5
                       and segment.addr <= section.addr
                       and section.addr + section.size <= segment.addr + segment.size
                       for segment in part.segments):
                continue
            body = data[section.offset : section.offset + section.size]
            for rule in rules:
                if rule["arch"] != part.arch:
                    continue
                expected = bytes.fromhex(rule["expected"])
                cursor = 0
                while (found := body.find(expected, cursor)) >= 0:
                    cursor = found + 1
                    address = section.addr + found
                    if address not in part.function_starts or (part.arch == "arm64" and address % 4):
                        rejected += 1
                        continue
                    if not part.uuid:
                        raise CompatibilityError(f"{part.arch}: missing Mach-O UUID.")
                    candidates.append({
                        "id": rule["id"], "arch": part.arch, "uuid": part.uuid,
                        "address": address, "expected": rule["expected"],
                        "patch_offset": rule["patch_offset"], "replacement": rule["replacement"],
                    })
        # Even byte-identical profile aliases are ambiguous configuration errors.
        status = "matched" if len(candidates) == 1 else "ambiguous" if candidates else "unsupported"
        diagnostics.append({"arch": part.arch, "status": status, "matches": len(candidates),
                            "rejected_non_function_matches": rejected})
        if len(candidates) == 1:
            hooks.append(candidates[0])
    return hooks, diagnostics


def analyze(app: Path, image_relative: str | None = None, *, features: list[str] | None = None) -> dict:
    try:
        features = selected_features(features)
    except ValueError as error:
        raise CompatibilityError(str(error)) from error
    app = app.resolve(strict=True)
    info = app_info(app)
    if (app / "Contents/Resources/WeChatTool/plan.json").exists():
        raise CompatibilityError("This bundle already contains WeChatTool. Analyze a clean official copy.")
    launcher_path = confined_path(app, "Contents/MacOS/WeChat")
    launcher = MachO(launcher_path.read_bytes())
    arches = [part.arch for part in launcher.slices]
    if image_relative is not None:
        # Validate explicit paths even when they do not exist; no traversal probes.
        confined_path(app, image_relative)
    candidates = [image_relative] if image_relative else [
        "Contents/Resources/wechat.dylib", "Contents/Frameworks/wechat.dylib"
    ]
    hooks, diagnostics, images = [], [], []
    for relative in candidates:
        if not (app / relative).exists():
            continue
        path = confined_path(app, relative)
        if path == launcher_path:
            raise CompatibilityError("Main-executable hooks are unsupported: the runtime only patches newly loaded images.")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        image_hooks = []
        for feature in features:
            if feature == "recall":
                found, details = scan_image(data, profiles())
            else:
                try:
                    found, details = scan_accessibility(data, accessibility_profiles(),
                                                       info["CFBundleShortVersionString"],
                                                       info["CFBundleVersion"], digest)
                except ValueError as error:
                    raise CompatibilityError(str(error)) from error
            diagnostics.extend({"image": relative, "feature": feature, **detail} for detail in details)
            image_hooks.extend({"feature": feature, **hook} for hook in found)
        if image_hooks:
            images.append({"path": relative, "sha256": digest})
            hooks.extend({"image": relative, "image_sha256": digest, **hook} for hook in image_hooks)
    problems = []
    for feature in features:
        required = 1 if feature == "recall" else 2
        for arch in arches:
            matching = [hook for hook in hooks if hook["arch"] == arch and hook["feature"] == feature]
            if len(matching) != required:
                problems.append(f"{arch} {feature}: expected {required} unique hook(s) across images, "
                                f"found {len(matching)}")
    if any(detail["status"] == "ambiguous" for detail in diagnostics):
        problems.append("At least one image has ambiguous matches; refusing all hooks.")
    if not arches:
        problems.append("No supported executable architectures.")
    notice_arches = []
    if not problems:
        for hook in hooks:
            if hook["feature"] != "recall":
                continue
            adapter = notice_adapter(hook, info["CFBundleShortVersionString"], info["CFBundleVersion"])
            if adapter:
                hook["notice_adapter"] = adapter
                notice_arches.append(hook["arch"])
    return {
        "schema_version": 1, "bundle_id": info["CFBundleIdentifier"],
        "version": info["CFBundleShortVersionString"], "build": info["CFBundleVersion"],
        "executable": "WeChat", "architectures": arches, "features": features,
        "status": "structurally-compatible" if not problems else "unsupported",
        "validation": "static-only; selected features require live validation",
        "runtime_requirement": "target image must load after plugin initialization",
        "recall_notices": {"status": ("not-selected" if "recall" not in features else
                                      "available" if notice_arches else "unavailable"),
                           "architectures": notice_arches,
                           "validation": "reviewed metadata layout; live recall notice test required"},
        "hooks": hooks if not problems else [], "images": images,
        "diagnostics": diagnostics, "problems": problems,
    }
