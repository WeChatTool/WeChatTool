# Building and distributing the Mac installer

This page is for release maintainers. People using a packaged installer do not
need Python, Xcode, Terminal, or a compiler. The installer contains a native Mac
interface, the prebuilt plugin, and a private Python runtime running the same
backend as the command-line tool. It does not include WeChat or any user data.

The [v0.1.0 release](https://github.com/WeChatTool/WeChatTool/releases/tag/v0.1.0)
provides a [Universal installer ZIP](https://github.com/WeChatTool/WeChatTool/releases/download/v0.1.0/WeChatTool-Installer-0.1.0-universal2.zip)
for Apple Silicon and Intel Macs running macOS 14 or later. It is ad-hoc signed
and has not been notarized by Apple. Installation steps are in the
[English README](../README.md) and [简体中文说明](../README.zh-CN.md).

## Build a local installer

Use macOS with Xcode Command Line Tools and Python 3.10 or newer. Run from the
repository root:

```sh
python3 -m venv build/installer-venv
build/installer-venv/bin/python -m pip install -r installer/requirements-build.txt
build/installer-venv/bin/python scripts/build-installer.py
```

The build uses the current Python architecture by default. Outputs are under
`dist/arm64/` or `dist/x86_64/`:

- `WeChatTool-Installer.app`: double-clickable installer.
- `WeChatTool-Installer-<version>-<architecture>.zip`: archive to download.
- A SHA-256 checksum and `build-info.json` recording the runtime and minimum OS.

Outputs are ignored by Git. Move previous output elsewhere before rebuilding;
the build command refuses to overwrite it. Build dependencies stay in the local
virtual environment. End users never install them.

The default build is ad-hoc signed and **not notarized**. Gatekeeper may block
it after download. For a trusted download, users can attempt to open the app,
then choose **System Settings → Privacy & Security → Open Anyway**, as described
in [Apple’s instructions](https://support.apple.com/en-sg/guide/mac-help/mh40616/mac).

## Processor and macOS support

Use `--arch arm64`, `--arch x86_64`, or `--arch universal2`. A Universal build
requires a Universal Python installation and Universal native dependencies;
selecting the flag alone cannot convert an Apple Silicon Python into one. See
[PyInstaller's macOS architecture documentation](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support).

```sh
build/installer-venv/bin/python scripts/build-installer.py --arch universal2
```

Alternatively, build each processor version in a matching Python environment.
The plugin itself is Universal in either case. The builder checks that every
bundled executable and library supports the advertised architectures and does
not depend on libraries at external Homebrew or build-machine paths.

The native interface targets macOS 11, but the bundled Python and its libraries
may require a newer version. The builder inspects their Mach-O deployment
versions and sets the app's minimum OS to the highest requirement. Publish that
minimum from `build-info.json`; do not assume every build supports macOS 11.
Test on the oldest macOS and each architecture you intend to support.

You can raise the GUI deployment target with `--minimum-macos 14.0`, for example
if your Swift toolchain cannot link Intel support libraries for older systems.
This never lowers a requirement imposed by the bundled runtime.

## Verify the package

```sh
python3 tests/installer_smoke.py "dist/arm64/WeChatTool-Installer.app"
```

Use the corresponding path for Intel or Universal builds. For an Intel test on
an Apple Silicon Mac with Rosetta:

```sh
python3 tests/installer_smoke.py "dist/universal2/WeChatTool-Installer.app" --arch x86_64
```

The test uses disposable synthetic apps. It checks the frozen backend from a
directory outside the source tree with a system-only executable search path,
including preparing and signing a copy, activation, refusal cases, and keeping
the source unchanged. It never launches the installed WeChat or reads chats.

Also open the installer to check English and Chinese layout, file selection,
compatibility results, error messages, and the normal quit/open workflow. Test a
downloaded archive on a clean Mac without developer tools. A successful static
compatibility check does not replace a real message-recall test for a WeChat
release.

## Sign and notarize a public build

For the usual macOS download experience, use an Apple Developer ID Application
certificate and Apple's notarization service. See [Apple's distribution
instructions](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
The builder supports signing the nested runtime, plugin, and installer with a
provided identity:

```sh
build/installer-venv/bin/python scripts/build-installer.py \
  --identity "Developer ID Application: Your Name (TEAMID)"
```

Configure your notarization credentials in Keychain separately. Submit the ZIP,
wait for acceptance, and staple the ticket to the app:

```sh
xcrun notarytool submit "dist/arm64/WeChatTool-Installer-0.1.0-arm64.zip" \
  --keychain-profile "WeChatTool-notary" --wait
xcrun stapler staple "dist/arm64/WeChatTool-Installer.app"
xcrun stapler validate "dist/arm64/WeChatTool-Installer.app"
spctl --assess --type execute --verbose "dist/arm64/WeChatTool-Installer.app"
```

Adjust the version and architecture for your build. After stapling, make a new
ZIP containing the stapled app and generate a checksum for **that** archive:

```sh
ditto -c -k --sequesterRsrc --keepParent "dist/arm64/WeChatTool-Installer.app" \
  "dist/arm64/WeChatTool-Installer-0.1.0-arm64-notarized.zip"
shasum -a 256 "dist/arm64/WeChatTool-Installer-0.1.0-arm64-notarized.zip" \
  > "dist/arm64/WeChatTool-Installer-0.1.0-arm64-notarized.zip.sha256"
```

The embedded build metadata records the original build and says
`notarized: false`; notarization happens afterward and is verified with the
stapled ticket. Do not edit signed bundle contents to change that field.
Notarization acceptance is not guaranteed. Signing the installer also does not
restore the official publisher identity of the WeChat copy it creates.

## Release contents

Publish installer archives and checksums as release assets, not as Git-tracked
binaries. State the processor, minimum macOS, WeChat versions actually tested,
and whether the download is signed and notarized. Include a short installation
guide linking to the English and Chinese READMEs.

The builder includes this project's license and notice plus the Python and
PyInstaller licenses. Review dependencies of the Python distribution you use;
custom or Homebrew Python builds may pull in additional libraries. Include any
additional notices using repeated `--runtime-license /path/to/notice` arguments
and meet those libraries' distribution requirements before publishing. The
binary inventory in `build-info.json` helps identify what was bundled.

Never include an installed or prepared WeChat app, compatibility reports from a
user's machine, logs, account data, or chat backups in a release.
