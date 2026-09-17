#!/usr/bin/env python3
"""Bundle the native installer, plugin, and Python backend into a Mac app."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechattool import __version__

MACHO_MAGIC = {bytes.fromhex(value) for value in
               ("cffaedfe", "feedfacf", "cefaedfe", "feedface", "cafebabe", "bebafeca", "cafebabf", "bfbafeca")}


def run(*args: str | Path, capture: bool = False) -> str:
    result = subprocess.run([str(arg) for arg in args], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout or ""


def binaries(directory: Path) -> list[Path]:
    result = []
    for path in directory.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in MACHO_MAGIC:
                    result.append(path)
    return sorted(result, key=lambda path: len(path.parts), reverse=True)


def version_tuple(value: str) -> tuple[int, int, int]:
    numbers = tuple(int(part) for part in value.split("."))
    return (numbers + (0, 0, 0))[:3]


def inspect_runtime(app: Path, architectures: set[str]) -> tuple[str, list[dict]]:
    minimum = (11, 0, 0)
    inventory = []
    for path in binaries(app):
        actual = set(run("/usr/bin/lipo", "-archs", path, capture=True).split())
        if not architectures.issubset(actual):
            raise RuntimeError(f"{path.name} is missing architectures {architectures - actual}")
        commands = run("/usr/bin/otool", "-l", path, capture=True)
        versions = re.findall(r"\bminos\s+(\d+(?:\.\d+)+)", commands)
        versions += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\d+(?:\.\d+)+)", commands)
        if not versions:
            raise RuntimeError(f"Cannot determine minimum macOS for {path.name}")
        dependencies = run("/usr/bin/otool", "-L", path, capture=True)
        for line in dependencies.splitlines():
            if " (compatibility version " not in line:
                continue
            dependency = line.strip().split(" (compatibility version ", 1)[0]
            if not dependency.startswith(("@rpath/", "@loader_path/", "@executable_path/",
                                          "/System/Library/", "/usr/lib/")):
                raise RuntimeError(f"Unbundled dependency in {path.name}: {dependency}")
        minimum = max(minimum, *(version_tuple(value) for value in versions))
        inventory.append({"path": str(path.relative_to(app)), "architectures": sorted(actual),
                          "minimum_macos": max(versions, key=version_tuple)})
    return ".".join(map(str, minimum)), inventory


def copy_licenses(resources: Path, extra: list[Path]) -> None:
    directory = resources / "Licenses"
    directory.mkdir()
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, directory / f"WeChatTool-{name}.txt")
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python's LICENSE.txt is missing; use a complete Python installation.")
    shutil.copy2(python_license, directory / "Python-LICENSE.txt")
    distribution = importlib.metadata.distribution("pyinstaller")
    for entry in distribution.files or []:
        if entry.name == "COPYING.txt":
            shutil.copy2(distribution.locate_file(entry), directory / "PyInstaller-COPYING.txt")
            break
    else:
        raise RuntimeError("The PyInstaller license was not found.")
    for index, source in enumerate(extra):
        shutil.copy2(source, directory / f"Runtime-{index + 1}-{source.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64", "universal2"), default=platform.machine())
    parser.add_argument("--minimum-macos", default="11.0",
                        help="GUI deployment target; raised automatically to the bundled runtime's minimum")
    parser.add_argument("--identity", help="Developer ID Application signing identity; default is local ad-hoc signing")
    parser.add_argument("--runtime-license", type=Path, action="append", default=[],
                        help="Additional license/notice for a dependency in the build Python; repeat as needed")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Building the installer requires macOS.")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", args.minimum_macos) or version_tuple(args.minimum_macos) < (11, 0, 0):
        parser.error("--minimum-macos must be a macOS version of 11.0 or later.")
    try:
        installed = importlib.metadata.version("pyinstaller")
    except importlib.metadata.PackageNotFoundError:
        parser.error("Install installer/requirements-build.txt in the build environment first.")
    if installed != "6.22.0":
        parser.error("Use the version pinned in installer/requirements-build.txt.")
    architectures = {"arm64", "x86_64"} if args.arch == "universal2" else {args.arch}
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    output = ROOT / "dist" / args.arch
    output.mkdir(parents=True, exist_ok=True)
    final = output / "WeChatTool Installer.app"
    archive = output / f"WeChatTool-Installer-{__version__}-{args.arch}.zip"
    if final.exists() or archive.exists():
        parser.error(f"Build output already exists in {output}. Move it elsewhere before rebuilding.")
    with tempfile.TemporaryDirectory(prefix="installer-", dir=build) as temporary:
        scratch = Path(temporary)
        app = scratch / "WeChatTool Installer.app"
        resources = app / "Contents/Resources"
        macos = app / "Contents/MacOS"
        resources.mkdir(parents=True)
        macos.mkdir()
        freeze = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
                  "--name", "wechattool-backend", "--target-arch", args.arch,
                  "--paths", str(ROOT), "--add-data", f"{ROOT / 'wechattool/profiles.json'}:wechattool",
                  "--add-data", f"{ROOT / 'wechattool/notice_profiles.json'}:wechattool",
                  "--distpath", str(scratch / "frozen"), "--workpath", str(scratch / "work"),
                  "--specpath", str(scratch)]
        # The backend needs no networking or optional archive formats. Avoid
        # pulling in unrelated SSL/compression libraries from the build Python.
        for module in ("_ssl", "_hashlib", "_lzma", "_bz2", "tkinter", "unittest"):
            freeze += ["--exclude-module", module]
        if args.identity:
            freeze += ["--codesign-identity", args.identity]
        run(*freeze, ROOT / "installer/backend.py")
        shutil.copytree(scratch / "frozen/wechattool-backend", resources / "backend", symlinks=True)
        run(ROOT / "scripts/build-plugin.sh", resources)
        runtime_minimum, _ = inspect_runtime(app, architectures)
        gui_minimum = max(runtime_minimum, args.minimum_macos, key=version_tuple)
        slices = []
        for arch in sorted(architectures):
            executable = scratch / f"Installer-{arch}"
            run("xcrun", "swiftc", "-O", "-swift-version", "5", "-target", f"{arch}-apple-macosx{gui_minimum}",
                "-framework", "AppKit", ROOT / "installer/main.swift", "-o", executable)
            slices.append(executable)
        run("/usr/bin/lipo", "-create", *slices, "-output", macos / "WeChatToolInstaller")
        minimum, inventory = inspect_runtime(app, architectures)
        metadata = {
            "CFBundleExecutable": "WeChatToolInstaller", "CFBundleIdentifier": "local.wechattool.installer",
            "CFBundleName": "WeChatTool Installer", "CFBundleDisplayName": "WeChatTool Installer",
            "CFBundlePackageType": "APPL", "CFBundleShortVersionString": __version__, "CFBundleVersion": "1",
            "LSMinimumSystemVersion": minimum, "LSApplicationCategoryType": "public.app-category.utilities",
            "NSHighResolutionCapable": True, "CFBundleDevelopmentRegion": "en",
            "CFBundleLocalizations": ["en", "zh-Hans"],
        }
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(metadata))
        copy_licenses(resources, args.runtime_license)
        report = {"version": __version__, "architecture": args.arch, "minimum_macos": minimum,
                  "python": platform.python_version(), "pyinstaller": installed,
                  "signing": "Developer ID" if args.identity else "ad-hoc (local testing)",
                  "notarized": False, "binaries": inventory}
        (resources / "build-info.json").write_text(json.dumps(report, indent=2) + "\n")
        signing = ["/usr/bin/codesign", "--force", "--sign", args.identity or "-"]
        signing += ["--options", "runtime", "--timestamp"] if args.identity else ["--timestamp=none"]
        for binary in binaries(app):
            run(*signing, binary)
        for framework in sorted(app.rglob("*.framework"), key=lambda path: len(path.parts), reverse=True):
            if not framework.is_symlink():
                run(*signing, framework)
        run(*signing, app)
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
        # Publish only the completely assembled, verified app.
        app.rename(final)
    run("/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", final, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n")
    (output / "build-info.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nBuilt {final}\nArchive: {archive}\nRequires macOS {minimum}+; {args.arch}.")
    if not args.identity:
        print("Local test build only: it has not been Developer ID signed or notarized.")


if __name__ == "__main__":
    main()
