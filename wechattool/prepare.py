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
from .macho import MachO, inject_dylib

INSTALL_NAME = "@executable_path/../Resources/WeChatTool/WeChatTool.dylib"


def run(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, check=True, capture_output=True)


def sign_copy(app: Path, plugin: Path, scratch: Path) -> None:
    # Extract the original sandbox/device/file permissions instead of silently
    # deleting the sandbox or enabling debugger attachment.
    result = run("/usr/bin/codesign", "-d", "--entitlements", ":-", str(app))
    entitlements = plistlib.loads(result.stdout) if result.stdout.strip() else {}
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


def prepare(source: Path, destination: Path, plugin: Path, *, image_relative: str | None = None) -> dict:
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
    plan = analyze(source, image_relative)
    if plan["status"] != "structurally-compatible":
        raise CompatibilityError("No safe plan: " + "; ".join(plan["problems"]))
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
        if analyze(staged, image_relative) != plan:
            raise CompatibilityError("Source changed during preparation. Retry after its update completes.")
        launcher = confined_path(staged, "Contents/MacOS/WeChat")
        if launcher.read_bytes() != original_launcher:
            raise CompatibilityError("Launcher changed during preparation.")
        resources = staged / "Contents/Resources/WeChatTool"
        if resources.exists() or resources.is_symlink():
            raise CompatibilityError("Unexpected existing WeChatTool directory.")
        resources.mkdir()
        copied_plugin = resources / "WeChatTool.dylib"
        shutil.copy2(plugin, copied_plugin)
        (resources / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        launcher.write_bytes(inject_dylib(original_launcher, INSTALL_NAME))
        sign_copy(staged, copied_plugin, scratch)
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
            "status": "prepared-and-signature-verified", "live_test": "not performed"}
