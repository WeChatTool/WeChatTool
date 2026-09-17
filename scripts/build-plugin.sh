#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail

task_root="$(cd "$(dirname "$0")/.." && pwd)"
output_dir="${1:-$task_root/build}"
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
task_build_dir="$(mktemp -d "${TMPDIR:-/tmp}/wechattool-build.XXXXXX")"
trap 'rm -rf "$task_build_dir"' EXIT

"${PYTHON:-python3}" "$task_root/scripts/compile-notice-profiles.py" "$task_build_dir/NoticeProfiles.inc"

for architecture in arm64 x86_64; do
    xcrun --sdk macosx clang++ -arch "$architecture" -mmacosx-version-min=11.0 \
        -std=c++17 -fobjc-arc -fvisibility=hidden -Wall -Wextra -Werror -O2 \
        -dynamiclib -framework Foundation -I "$task_build_dir" \
        -Wl,-install_name,@rpath/WeChatTool.dylib \
        "$task_root/native/WeChatTool.mm" "$task_root/native/RecallNotice.mm" \
        "$task_root/native/RecallRuntime.mm" \
        -o "$task_build_dir/$architecture.dylib"
done
xcrun lipo -create "$task_build_dir/arm64.dylib" "$task_build_dir/x86_64.dylib" \
    -output "$output_dir/WeChatTool.dylib"
codesign --force --sign - "$output_dir/WeChatTool.dylib"
codesign --verify --strict "$output_dir/WeChatTool.dylib"
printf 'Built %s\n' "$output_dir/WeChatTool.dylib"
xcrun lipo -archs "$output_dir/WeChatTool.dylib"
