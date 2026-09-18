"""Create a separately signed app copy; never patch the source installation."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from .analyze import CompatibilityError, analyze, confined_path
from .instances import (InstanceIdentity, create_identity, isolated_entitlements,
                        isolated_file_provider_entitlements, isolated_file_provider_info,
                        isolated_info)
from .macho import MachO, inject_dylib

INSTALL_NAME = "@executable_path/../Resources/WeChatTool/WeChatTool.dylib"


def run(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=True, capture_output=True)


def set_localized_display_name(app: Path, display_name: str) -> None:
    """Use the chosen app name in every main-bundle language, preserving translations."""
    app = app.resolve(strict=True)
    if (not isinstance(display_name, str) or not display_name.strip() or "/" in display_name
            or any(ord(character) < 32 or ord(character) == 127 for character in display_name)):
        raise CompatibilityError("Invalid application display name.")
    resources = confined_path(app, "Contents/Resources")
    replacements = []
    for path in sorted(resources.glob("*.lproj/InfoPlist.strings")):
        path = confined_path(app, str(path.relative_to(app)))
        # Official .strings files may be UTF-16 OpenStep plists; plistlib alone
        # cannot read that format. plutil reads without modifying the input.
        converted = run("/usr/bin/plutil", "-convert", "xml1", "-o", "-", str(path))
        strings = plistlib.loads(converted.stdout)
        if not isinstance(strings, dict):
            raise CompatibilityError("Localized application metadata must contain a dictionary.")
        strings.update(CFBundleName=display_name, CFBundleDisplayName=display_name)
        replacements.append((path, plistlib.dumps(strings, fmt=plistlib.FMT_BINARY)))
    for path, content in replacements:
        path.write_bytes(content)


def sign_copy(app: Path, plugin: Path, scratch: Path, identity: InstanceIdentity) -> None:
    # Extract the original sandbox/device/file permissions instead of silently
    # deleting the sandbox or enabling debugger attachment.
    result = run("/usr/bin/codesign", "-d", "--entitlements", ":-", str(app))
    entitlements = isolated_entitlements(
        plistlib.loads(result.stdout) if result.stdout.strip() else {}, identity)
    entitlements.update({
        "com.apple.security.cs.disable-library-validation": True,
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
    })
    entitlements_file = scratch / "entitlements.plist"
    entitlements_file.write_bytes(plistlib.dumps(entitlements))
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(plugin))
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none",
        "--options", "runtime", "--entitlements", str(entitlements_file), str(app))
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    signed = run("/usr/bin/codesign", "-d", "--entitlements", ":-", str(app))
    if plistlib.loads(signed.stdout) != entitlements:
        raise CompatibilityError("Signed copy did not retain its independent storage permissions.")


def isolate_helpers(app: Path, identity: InstanceIdentity, scratch: Path) -> list[str]:
    """Keep inherited helpers and give FileProvider only its new host's storage."""
    omitted = []
    isolated = {}
    plugins = app / "Contents/PlugIns"
    if plugins.is_symlink():
        raise CompatibilityError("Cannot isolate a symlinked system-extension directory.")
    if plugins.exists():
        for extension in sorted(plugins.glob("*.appex")):
            if extension.is_symlink():
                raise CompatibilityError("Cannot isolate a symlinked system extension.")
            if extension.name == "WeChatMacShare.appex":
                # Its hardcoded official-app wakeup and destination-free IPC
                # cannot safely route a share to an independent installation.
                omitted.append(extension.name)
                shutil.rmtree(extension)
                continue
            if extension.name != "WeChatFileProviderExtension.appex":
                raise CompatibilityError(f"Unsupported system extension: {extension.name}.")
            info = confined_path(extension, "Contents/Info.plist")
            metadata = isolated_file_provider_info(plistlib.loads(info.read_bytes()), identity)
            original = run("/usr/bin/codesign", "-d", "--entitlements", ":-", str(extension))
            grants = isolated_file_provider_entitlements(
                plistlib.loads(original.stdout) if original.stdout.strip() else {}, identity)
            info.write_bytes(plistlib.dumps(metadata))
            grant_file = scratch / "file-provider-entitlements.plist"
            grant_file.write_bytes(plistlib.dumps(grants))
            run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none",
                "--options", "runtime", "--entitlements", str(grant_file), str(extension))
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(extension))
            isolated[extension.resolve(strict=True)] = grants
    # An inherited helper runs inside its parent's sandbox. A separate helper
    # carrying the publisher's storage grants would defeat per-copy isolation.
    checked = set()
    for info in app.rglob("Info.plist"):
        bundle = info.parent.parent
        if info.parent.name != "Contents" or bundle == app or bundle.suffix not in {".app", ".xpc", ".appex"}:
            continue
        resolved = bundle.resolve(strict=True)
        if not resolved.is_relative_to(app.resolve(strict=True)):
            raise CompatibilityError("A helper bundle escapes the prepared app.")
        if resolved in checked:
            continue
        checked.add(resolved)
        result = run("/usr/bin/codesign", "-d", "--entitlements", ":-", str(bundle))
        grants = plistlib.loads(result.stdout) if result.stdout.strip() else {}
        if resolved in isolated:
            if grants != isolated[resolved]:
                raise CompatibilityError("FileProvider did not retain its independent storage permissions.")
            continue
        shared = ("com.apple.security.application-groups", "keychain-access-groups",
                  "com.apple.application-identifier", "application-identifier")
        if any(grants.get(key) for key in shared) or (
                grants.get("com.apple.security.app-sandbox") and
                grants.get("com.apple.security.inherit") is not True):
            raise CompatibilityError(
                f"Bundled helper {bundle.name} has independent storage permissions; "
                "this build cannot safely isolate it.")
    return omitted


def prepare(source: Path, destination: Path, plugin: Path, *, image_relative: str | None = None,
            features: list[str] | None = None) -> dict:
    source = source.resolve(strict=True)
    destination = destination.expanduser().absolute()
    # Do not replace an existing app, even a previous output. Every output is reviewable.
    if destination.exists() or destination.is_symlink():
        raise CompatibilityError(f"Destination already exists: {destination}. Choose a new path.")
    destination = destination.resolve()
    if destination.suffix != ".app":
        raise CompatibilityError("Destination must end in .app.")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise CompatibilityError("Source and destination app trees must be separate.")
    plugin = plugin.resolve(strict=True)
    plan = analyze(source, image_relative, features=features)
    if plan["status"] != "structurally-compatible":
        raise CompatibilityError("No safe plan: " + "; ".join(plan["problems"]))
    original_info = confined_path(source, "Contents/Info.plist").read_bytes()
    source_info = plistlib.loads(original_info)
    identity = create_identity(source_info)
    plugin_arches = {part.arch for part in MachO(plugin.read_bytes()).slices}
    if not set(plan["architectures"]).issubset(plugin_arches):
        raise CompatibilityError("Plugin is missing one or more executable architectures; rebuild universally.")
    original_launcher = confined_path(source, "Contents/MacOS/WeChat").read_bytes()
    # Fail on insufficient header space before copying a large bundle.
    inject_dylib(original_launcher, INSTALL_NAME)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wechattool-", dir=destination.parent) as directory:
        scratch = Path(directory)
        staged = scratch / destination.name
        run("/usr/bin/ditto", str(source), str(staged))
        # Re-analyze the copy: an app update while copying must never reuse an old plan.
        if analyze(staged, image_relative, features=plan["features"]) != plan:
            raise CompatibilityError("Source changed during preparation. Retry after its update completes.")
        info_path = confined_path(staged, "Contents/Info.plist")
        if info_path.read_bytes() != original_info:
            raise CompatibilityError("Application identity changed during preparation.")
        launcher = confined_path(staged, "Contents/MacOS/WeChat")
        if launcher.read_bytes() != original_launcher:
            raise CompatibilityError("Launcher changed during preparation.")
        resources = staged / "Contents/Resources/WeChatTool"
        if resources.exists() or resources.is_symlink():
            raise CompatibilityError("Unexpected existing WeChatTool directory.")
        resources.mkdir()
        copied_plugin = resources / "WeChatTool.dylib"
        shutil.copy2(plugin, copied_plugin)
        omitted_extensions = isolate_helpers(staged, identity, scratch)
        plan.update(identity.to_dict())
        plan["data_isolation"] = "per-installation"
        info_path.write_bytes(plistlib.dumps(isolated_info(source_info, identity, destination.stem)))
        set_localized_display_name(staged, destination.stem)
        (resources / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        launcher.write_bytes(inject_dylib(original_launcher, INSTALL_NAME))
        sign_copy(staged, copied_plugin, scratch, identity)
        # Signing may alter signatures, never the core code we're planning to hook.
        for image in plan["images"]:
            if image["path"] == "Contents/MacOS/WeChat":
                continue  # Launcher signature/load commands intentionally changed.
            current = hashlib.sha256(confined_path(staged, image["path"]).read_bytes()).hexdigest()
            if current != image["sha256"]:
                raise CompatibilityError("Core image changed while signing; refusing to publish output.")
        # Avoid overwrite even if another preparation finished during the copy.
        # macOS renamex_np(RENAME_EXCL) is atomic and refuses any existing target.
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(staged), os.fsencode(destination), 0x00000004) != 0:
            raise OSError(ctypes.get_errno(), "Could not publish app without overwriting destination")
    return {"destination": str(destination), "version": plan["version"], "build": plan["build"],
            "features": plan["features"],
            **identity.to_dict(), "data_isolation": "per-installation",
            "omitted_system_extensions": omitted_extensions,
            "status": "prepared-and-signature-verified", "live_test": "not performed"}
