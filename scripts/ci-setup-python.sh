#!/bin/bash
# SPDX-License-Identifier: MIT
# Bootstrap the private universal2 packaging runtime on disposable CI runners.
# Never run an installer on a developer machine or a self-hosted runner.
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != "true" || "${RUNNER_ENVIRONMENT:-}" != "github-hosted" ||
      "$(/usr/bin/uname -s)" != "Darwin" ]]; then
    printf '%s\n' 'Refusing installation: requires a GitHub-hosted macOS Actions runner.' >&2
    exit 2
fi

task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
build_dir="$task_root/build"
venv_dir="$build_dir/ci-venv"
license_dir="$build_dir/ci-licenses"
python_root='/Library/Frameworks/Python.framework/Versions/3.13'
python_binary="$python_root/bin/python3.13"
python_version='3.13.15'
package_sha256='3b7eaf7f29825f796e8267024435540ddf1f17fc9a97ad58095daa7a75bfdcd3'
source_sha256='1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76'

if [[ -L "$build_dir" || -e "$venv_dir" || -L "$venv_dir" ||
      -e "$license_dir" || -L "$license_dir" ]]; then
    printf '%s\n' 'Refusing to replace existing CI output or use a symlinked build directory.' >&2
    exit 2
fi
/bin/mkdir -p "$build_dir"
task_temp="$(/usr/bin/mktemp -d "$build_dir/.ci-python.XXXXXXXX")"
created_venv=0
created_licenses=0
cleanup() {
    status=$?
    trap - EXIT
    /bin/rm -rf -- "$task_temp"
    if [[ "$status" != 0 ]]; then
        if [[ "$created_venv" == 1 ]]; then /bin/rm -rf -- "$venv_dir"; fi
        if [[ "$created_licenses" == 1 ]]; then /bin/rm -rf -- "$license_dir"; fi
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

download_verified() {
    local url="$1" destination="$2" expected="$3" actual
    /usr/bin/curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --connect-timeout 30 --max-time 600 --retry 3 \
        --output "$destination" "$url"
    actual="$(/usr/bin/shasum -a 256 "$destination")"
    actual="${actual%% *}"
    if [[ "$actual" != "$expected" ]]; then
        printf 'SHA-256 mismatch for %s\n' "$url" >&2
        return 1
    fi
}

package="$task_temp/python-$python_version-macos11.pkg"
source_archive="$task_temp/Python-$python_version.tar.xz"
download_verified "https://www.python.org/ftp/python/$python_version/python-$python_version-macos11.pkg" \
    "$package" "$package_sha256"
download_verified "https://www.python.org/ftp/python/$python_version/Python-$python_version.tar.xz" \
    "$source_archive" "$source_sha256"

# Both upstream artifacts are pinned and verified before any system mutation.
# sudo -n also prevents an unexpected interactive password prompt in CI.
/usr/bin/sudo -n /usr/sbin/installer -pkg "$package" -target /
/usr/bin/lipo "$python_binary" -verify_arch arm64 x86_64
/usr/bin/lipo "$python_root/Python" -verify_arch arm64 x86_64
"$python_binary" -I - "$python_root" <<'PY'
from pathlib import Path
import sys
import sysconfig

if sys.version_info[:3] != (3, 13, 15):
    raise SystemExit(f"Unexpected installed Python: {sys.version}")
if Path(sys.base_prefix).resolve() != Path(sys.argv[1]).resolve():
    raise SystemExit("Python did not load the pinned framework installation.")
if sysconfig.get_config_var("PYTHONFRAMEWORK") != "Python":
    raise SystemExit("Expected the official Python framework build.")
if not (Path(sysconfig.get_path("stdlib")) / "LICENSE.txt").is_file():
    raise SystemExit("Installed runtime is missing its Python license.")
PY

# Claim fresh output paths. A venv is created at its final location because its
# script shebangs are absolute; moving a staged venv would leave broken tools.
/bin/mkdir "$venv_dir"
created_venv=1
/bin/mkdir "$license_dir"
created_licenses=1
"$python_binary" -I -m venv "$venv_dir"
"$venv_dir/bin/python" -I -m pip --isolated --disable-pip-version-check --require-virtualenv install \
    --no-input --no-cache-dir --only-binary=:all: \
    -r "$task_root/installer/requirements-build.txt"
"$venv_dir/bin/python" -I -c \
    'import importlib.metadata; assert importlib.metadata.version("pyinstaller") == "6.22.0"'
/usr/bin/lipo "$venv_dir/bin/python" -verify_arch arm64 x86_64

"$venv_dir/bin/python" -I - "$source_archive" "$license_dir" "$task_root/installer/Python-Runtime-Packaging-NOTICE.txt" <<'PY'
from pathlib import Path
import hashlib
import re
import shutil
import sys
import tarfile

archive, output, packaging_notice = map(Path, sys.argv[1:])
expected = "1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76"
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit("Source archive changed after verification.")
prefix = "Python-3.13.15/"
source_url = "https://www.python.org/ftp/python/3.13.15/Python-3.13.15.tar.xz"
with tarfile.open(archive, "r:xz") as source:
    def read(name: str) -> bytes:
        member = source.getmember(prefix + name)
        if not member.isfile() or not 0 < member.size <= 8 * 1024 * 1024:
            raise ValueError(f"Invalid license source member: {name}")
        stream = source.extractfile(member)
        if stream is None:
            raise ValueError(f"Cannot read license source member: {name}")
        return stream.read()

    (output / "Python-History-and-Licenses.txt").write_bytes(read("Doc/license.rst"))
    (output / "Python-Expat-COPYING.txt").write_bytes(read("Modules/expat/COPYING"))

    def header_notices(names: list[str], required: str, destination: str) -> None:
        notices = {}
        for name in names:
            text = read(name).decode("utf-8")
            match = re.match(r"\s*(/\*.*?\*/)", text, re.DOTALL)
            if match is None or required not in match[1]:
                raise ValueError(f"Expected original license notice in {name}")
            notices.setdefault(match[1], []).append(name)
        sections = [f"Notices retained from CPython 3.13.15.\nSource: {source_url}\n"]
        for notice, names in notices.items():
            sections.append("Original source: " + ", ".join(names) + "\n\n" + notice + "\n")
        (output / destination).write_text("\n".join(sections), encoding="utf-8")

    # This pinned release has no Modules/_hacl/LICENSE.txt. Preserve the full
    # original license headers from the bundled hash implementations instead.
    header_notices([f"Modules/_hacl/Hacl_Hash_{name}.c" for name in ("MD5", "SHA1", "SHA2", "SHA3")],
                   "MIT License", "Python-HACL-LICENSE.txt")
    header_notices(["Modules/_blake2/impl/blake2.h", "Modules/_blake2/impl/blake2b.c",
                    "Modules/_blake2/impl/blake2s.c"],
                   "BLAKE2 reference source code", "Python-BLAKE2-LICENSE.txt")
shutil.copy2(packaging_notice, output / packaging_notice.name)
if len(list(output.glob("*.txt"))) != 5:
    raise SystemExit("Runtime license collection is incomplete.")
PY

printf 'Ready: %s\nRuntime license notices: %s\n' "$venv_dir/bin/python" "$license_dir"
printf '%s\n' 'Build with --arch universal2 --minimum-macos 14.0 and one --runtime-license for each ci-licenses/*.txt file.'
