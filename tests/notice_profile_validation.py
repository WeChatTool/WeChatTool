#!/usr/bin/env python3
"""Reject malformed reviewed profiles and exercise native selection on fake data."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def check_generator() -> int:
    spec = importlib.util.spec_from_file_location("notice_profile_compiler", ROOT / "scripts/compile-notice-profiles.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = json.loads((ROOT / "wechattool/notice_profiles.json").read_text())
    assert module.validate_document(source) == source
    cases = [None, [], {"schema_version": True, "adapters": []},
             {"schema_version": 1, "adapters": {}},
             {"schema_version": 1, "adapters": [None]},
             {**source, "unexpected": 1}]
    for key, value in (("id", []), ("id", "bad id"), ("version", ""), ("build", "a\x00b"),
                       ("arch", "aarch64"), ("layout", "guessed-layout"),
                       ("uuid", "bad-uuid"), ("image_sha256", "G" * 64),
                       ("predicate_address", True), ("predicate_address", 4.0),
                       ("predicate_address", -1), ("predicate_address", 2**64),
                       ("insert_notice", None), ("handler", None), ("task_slot", None)):
        malformed = copy.deepcopy(source)
        malformed["adapters"][0][key] = value
        cases.append(malformed)
    for region in ("insert_notice", "handler", "task_slot"):
        for key, value in (("address", 2**64 - 4), ("address", True), ("address", 0),
                           ("size", True), ("size", 65537), ("size", 0),
                           ("sha256", []), ("sha256", "a" * 63), ("unexpected", 1)):
            malformed = copy.deepcopy(source)
            malformed["adapters"][0][region][key] = value
            cases.append(malformed)
        missing = copy.deepcopy(source)
        del missing["adapters"][0][region]["size"]
        cases.append(missing)
    for key, value in (("expected", "00" * 31), ("expected", "GG" * 32), ("expected", None), ("size", 31)):
        malformed = copy.deepcopy(source)
        malformed["adapters"][0]["handler"][key] = value
        cases.append(malformed)
    duplicate_id = copy.deepcopy(source)
    duplicate_id["adapters"][1]["id"] = duplicate_id["adapters"][0]["id"]
    cases.append(duplicate_id)
    duplicate_identity = copy.deepcopy(source)
    alias = copy.deepcopy(duplicate_identity["adapters"][0])
    alias["id"] += "-alias"
    duplicate_identity["adapters"].append(alias)
    cases.append(duplicate_identity)
    for index, malformed in enumerate(cases):
        try:
            module.validate_document(malformed)
        except ValueError:
            continue
        raise AssertionError(f"Generator accepted malformed case {index}: {malformed!r}")
    return len(cases) + 1


HARNESS = r'''
#include "WeChatTool.mm"
#include <stdio.h>

static unsigned checks = 0;
static void Require(bool valid, const char *description) {
    ++checks;
    if (!valid) { fprintf(stderr, "FAIL: %s\n", description); exit(1); }
}
static NSArray *Decode(id value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingFragmentsAllowed error:nil];
    return DecodeNoticeProfiles(data);
}
static NSArray *Profiles(NSArray *profiles) {
    return Decode(@{@"schema_version": @1, @"adapters": profiles});
}
int main() {
    @autoreleasepool {
        @try {
            NSString *hash = [@"1" stringByPaddingToLength:64 withString:@"1" startingAtIndex:0];
            NSDictionary *profile = @{@"id": @"fixture", @"layout": kNoticeLayout,
                @"arch": kArchitecture, @"version": @"test-version", @"build": @"test-build",
                @"uuid": @"00000000-0000-0000-0000-000000000001", @"image_sha256": hash,
                @"predicate_address": @4096,
                @"insert_notice": @{@"address": @8192, @"size": @64, @"sha256": hash},
                @"task_slot": @{@"address": @12288, @"size": @16, @"sha256": hash},
                @"handler": @{@"address": @16384, @"size": @64, @"sha256": hash, @"expected": hash}};
            NSMutableDictionary *hook = [@{@"notice_adapter": @"fixture", @"arch": kArchitecture,
                @"uuid": profile[@"uuid"], @"image_sha256": hash, @"address": @4096} mutableCopy];
            Require(Profiles(@[profile]).count == 1, "valid compiled profile");
            Require(FindNoticeAdapter(hook, Profiles(@[profile])) != nil, "valid identity selected");
            for (id document in @[@[], @1, @{@"schema_version": @YES, @"adapters": @[]},
                                  @{@"schema_version": @1, @"adapters": @{} }])
                Require(Decode(document) == nil, "malformed document rejected");
            Require(DecodeNoticeProfiles([@"{broken" dataUsingEncoding:NSUTF8StringEncoding]) == nil,
                    "invalid JSON rejected");
            for (id entry in @[NSNull.null, @1, @[]])
                Require(Profiles(@[entry]) == nil, "malformed adapter rejected");
            NSDictionary *badFields = @{@"id": @1, @"version": NSNull.null, @"build": @[],
                @"arch": @"aarch64", @"layout": @"wrong-layout", @"uuid": @"invalid",
                @"image_sha256": [@"G" stringByPaddingToLength:64 withString:@"G" startingAtIndex:0],
                @"predicate_address": @YES, @"insert_notice": @[], @"handler": @[], @"task_slot": @[]};
            for (NSString *key in badFields) {
                NSMutableDictionary *bad = [profile mutableCopy]; bad[key] = badFields[key];
                Require(Profiles(@[bad]) == nil, "malformed profile field rejected");
            }
            NSArray *badProbes = @[@{}, @{@"address": @YES, @"size": @16, @"sha256": hash},
                @{@"address": @8192, @"size": @YES, @"sha256": hash},
                @{@"address": @8192, @"size": @0, @"sha256": hash},
                @{@"address": @8192, @"size": @65537, @"sha256": hash},
                @{@"address": @(UINT64_MAX - 3), @"size": @16, @"sha256": hash},
                @{@"address": @8192, @"size": @16, @"sha256": @1},
                @{@"address": @8192, @"size": @16, @"sha256": @"abc"}];
            for (NSString *region in @[@"insert_notice", @"handler", @"task_slot"]) {
                for (id probe in badProbes) {
                    NSMutableDictionary *bad = [profile mutableCopy];
                    NSMutableDictionary *entry = [probe mutableCopy];
                    if ([region isEqualToString:@"handler"]) entry[@"expected"] = hash;
                    bad[region] = entry;
                    Require(Profiles(@[bad]) == nil, "malformed function region rejected");
                }
            }
            for (id value in @[@1, NSNull.null, @"00", [@"G" stringByPaddingToLength:64 withString:@"G" startingAtIndex:0]]) {
                NSMutableDictionary *bad = [profile mutableCopy];
                NSMutableDictionary *entry = [profile[@"handler"] mutableCopy];
                entry[@"expected"] = value; bad[@"handler"] = entry;
                Require(Profiles(@[bad]) == nil, "malformed handler prefix rejected");
            }
            NSMutableDictionary *shortHandler = [profile mutableCopy];
            NSMutableDictionary *shortEntry = [profile[@"handler"] mutableCopy];
            shortEntry[@"size"] = @31; shortHandler[@"handler"] = shortEntry;
            Require(Profiles(@[shortHandler]) == nil, "handler smaller than verified prefix rejected");
            Require(Hex((NSString *)@1) == nil && Hex(@"0") == nil && Hex(@"gg") == nil &&
                    Hex(@"FF") == nil, "invalid hex rejected without exception");
            Require(Hex(@"01af").length == 2, "valid hex decoded");
            Require(FindNoticeAdapter(hook, Profiles(@[profile, profile])) == nil,
                    "duplicate compiled identity rejected");
            NSMutableDictionary *alias = [profile mutableCopy]; alias[@"id"] = @"alias";
            Require(FindNoticeAdapter(hook, Profiles(@[profile, alias])) == nil,
                    "duplicate identity with distinct ID rejected");
            alias[@"predicate_address"] = @4100;
            Require(FindNoticeAdapter(hook, Profiles(@[alias, profile])) != nil,
                    "unrelated identity does not prevent selection");
            hook[@"notice_adapter"] = @"unknown";
            Require(FindNoticeAdapter(hook, Profiles(@[profile])) == nil, "unknown adapter ID refused");
            printf("Native profile validation: %u checks passed (%s)\n", checks, kArchitecture.UTF8String);
        } @catch (NSException *exception) {
            fprintf(stderr, "Unexpected exception: %s\n", exception.name.UTF8String); return 1;
        }
    }
    return 0;
}
'''


def run(*arguments: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([str(arg) for arg in arguments], capture_output=True, text=True, timeout=120, **kwargs)
    if result.returncode:
        raise AssertionError(f"Command failed ({result.returncode}): {arguments[0]}\n{result.stdout}{result.stderr}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    args = parser.parse_args()
    print(f"Generator profile validation: {check_generator()} checks passed", flush=True)
    with tempfile.TemporaryDirectory(prefix="wechattool-profile-validation-") as temporary:
        directory = Path(temporary)
        contents = directory / "ProfileValidation.app/Contents"
        executable = contents / "MacOS/ProfileValidation"
        executable.parent.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "local.wechattool.profile-tests", "CFBundleExecutable": executable.name,
            "CFBundleShortVersionString": "test-version", "CFBundleVersion": "test-build",
            "CFBundlePackageType": "APPL"}))
        (directory / "NoticeProfiles.inc").write_text(
            'static const char kCompiledNoticeProfiles[] = "{\\"schema_version\\":1,\\"adapters\\":[]}";\n')
        harness = directory / "validation.mm"
        harness.write_text(HARNESS)
        run("/usr/bin/xcrun", "clang++", "-arch", args.arch, "-mmacosx-version-min=11.0",
            "-std=c++17", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-O2",
            "-framework", "Foundation", "-I", directory, "-I", ROOT / "native",
            harness, ROOT / "native/RecallNotice.mm", ROOT / "native/RecallRuntime.mm",
            "-o", executable)
        print(run("/usr/bin/arch", "-arch", args.arch, executable,
                  env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(directory),
                       "CFFIXED_USER_HOME": str(directory), "WECHATTOOL_DISABLE": "1"}).stdout, end="")


if __name__ == "__main__":
    main()
