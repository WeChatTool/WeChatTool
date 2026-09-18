// SPDX-License-Identifier: MIT
// Original implementation. Local notices use WeChat's own message services.
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#import <objc/runtime.h>
#import "RecallRuntime.h"
#include "NoticeProfiles.inc"
#include "AccessibilityProfiles.inc"
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <os/log.h>
#include <pthread.h>
#include <uuid/uuid.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

namespace {

#if defined(__arm64__)
static NSString *const kArchitecture = @"arm64";
static NSString *const kNoticeLayout = @"messagewrap-libcpp-alt-0x130";
#elif defined(__x86_64__)
static NSString *const kArchitecture = @"x86_64";
static NSString *const kNoticeLayout = @"messagewrap-libcpp-default-0x130";
#else
#error Unsupported architecture
#endif

struct Runtime {
    NSString *root;
    NSArray<NSDictionary *> *hooks;
    NSSet<NSValue *> *initialImages;
    NSMutableSet<NSString *> *finished;
    os_log_t log;
    pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
    NSUInteger applied = 0;
};

// Intentionally process-lifetime state: dyld retains the callback forever.
static Runtime *gRuntime = nullptr;

// The official Share extension broadcasts these channels without identifying
// a destination installation. Isolated copies must not receive its payloads.
static bool OfficialShareChannel(NSString *name) {
    return [name isEqualToString:@"wechat_share_to_wechat_channel"] ||
           [name isEqualToString:@"WeChatMacShare_Notification"];
}

using Observer4 = void (*)(id, SEL, id, SEL, NSString *, NSString *);
using Observer5 = void (*)(id, SEL, id, SEL, NSString *, NSString *, NSNotificationSuspensionBehavior);
static Observer4 gAddObserver4 = nullptr;
static Observer5 gAddObserver5 = nullptr;

static void IsolatedObserver4(id center, SEL command, id observer, SEL selector,
                              NSString *name, NSString *object) {
    if (!OfficialShareChannel(name))
        gAddObserver4(center, command, observer, selector, name, object);
}

static void IsolatedObserver5(id center, SEL command, id observer, SEL selector,
                              NSString *name, NSString *object, NSNotificationSuspensionBehavior behavior) {
    if (!OfficialShareChannel(name))
        gAddObserver5(center, command, observer, selector, name, object, behavior);
}

static bool IsolateShareNotifications() {
    Class type = NSDistributedNotificationCenter.class;
    SEL four = @selector(addObserver:selector:name:object:);
    SEL five = @selector(addObserver:selector:name:object:suspensionBehavior:);
    Method method4 = class_getInstanceMethod(type, four);
    Method method5 = class_getInstanceMethod(type, five);
    if (!method4 || !method5 || method_getNumberOfArguments(method4) != 6 ||
        method_getNumberOfArguments(method5) != 7) return false;
    char return4[8] = {}, return5[8] = {};
    method_getReturnType(method4, return4, sizeof(return4));
    method_getReturnType(method5, return5, sizeof(return5));
    if (strcmp(return4, "v") || strcmp(return5, "v")) return false;
    gAddObserver4 = reinterpret_cast<Observer4>(method_getImplementation(method4));
    gAddObserver5 = reinterpret_cast<Observer5>(method_getImplementation(method5));
    if (!gAddObserver4 || !gAddObserver5) return false;
    // Replace on this subclass, never an inherited NSNotificationCenter method.
    class_replaceMethod(type, four, reinterpret_cast<IMP>(IsolatedObserver4), method_getTypeEncoding(method4));
    class_replaceMethod(type, five, reinterpret_cast<IMP>(IsolatedObserver5), method_getTypeEncoding(method5));
    return class_getMethodImplementation(type, four) == reinterpret_cast<IMP>(IsolatedObserver4) &&
           class_getMethodImplementation(type, five) == reinterpret_cast<IMP>(IsolatedObserver5);
}

static void Log(os_log_t logger, NSString *status, NSString *identifier) {
    os_log_with_type(logger, OS_LOG_TYPE_DEFAULT,
                     "status=%{public}s hook=%{public}s arch=%{public}s",
                     status.UTF8String, identifier.UTF8String,
                     kArchitecture.UTF8String);
}

static bool String(id value, NSUInteger maximum = 256) {
    return [value isKindOfClass:NSString.class] && [value length] > 0 &&
           [value length] <= maximum;
}

static bool Boolean(id value) {
    return [value isKindOfClass:NSNumber.class] &&
           CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID();
}

static bool Integer(id value, uint64_t *result) {
    if (![value isKindOfClass:NSNumber.class] || Boolean(value)) return false;
    const char *text = [[value stringValue] UTF8String];
    if (!text || !*text) return false;
    for (const char *p = text; *p; ++p) if (*p < '0' || *p > '9') return false;
    errno = 0;
    char *end = nullptr;
    unsigned long long parsed = strtoull(text, &end, 10);
    if (errno || !end || *end) return false;
    *result = parsed;
    return true;
}

static NSString *Canonical(NSString *path) {
    char resolved[PATH_MAX];
    if (!realpath(path.fileSystemRepresentation, resolved)) return nil;
    return [[NSFileManager defaultManager] stringWithFileSystemRepresentation:resolved
                                                                    length:strlen(resolved)];
}

static bool RelativeImagePath(id path) {
    if (!String(path, 1024) || [path isAbsolutePath] || ![path hasPrefix:@"Contents/"])
        return false;
    for (NSString *part in [path componentsSeparatedByString:@"/"]) {
        if (!part.length || [part isEqualToString:@"."] || [part isEqualToString:@".."])
            return false;
    }
    return [path rangeOfString:@"\0"].location == NSNotFound;
}

// Recall plans can select locations only for these reviewed recipes; they
// cannot supply arbitrary machine code. Accessibility uses its compiled registry.
static bool AllowedRecallHook(NSDictionary *hook) {
    if (![hook isKindOfClass:NSDictionary.class] || !String(hook[@"id"], 128) ||
        !RelativeImagePath(hook[@"image"]) || !String(hook[@"uuid"], 36) ||
        !String(hook[@"arch"], 16) || !String(hook[@"expected"], 128) ||
        !String(hook[@"replacement"], 16) ||
        (hook[@"notice_adapter"] && !String(hook[@"notice_adapter"], 128))) return false;
    uuid_t uuid;
    uuid_string_t normalized;
    if (uuid_parse([hook[@"uuid"] UTF8String], uuid) != 0) return false;
    uuid_unparse_lower(uuid, normalized);
    if (![hook[@"uuid"] isEqualToString:@(normalized)]) return false;
    uint64_t address, offset;
    if (!Integer(hook[@"address"], &address) || !address || address > UINTPTR_MAX ||
        !Integer(hook[@"patch_offset"], &offset)) return false;
    if ([hook[@"arch"] isEqualToString:@"arm64"]) {
        return address % 4 == 0 && offset == 12 &&
            [hook[@"expected"] isEqualToString:@"080c40b949e284521f01096be0179f1ac0035fd6"] &&
            [hook[@"replacement"] isEqualToString:@"00008052"];
    }
    if ([hook[@"arch"] isEqualToString:@"x86_64"]) {
        return offset == 11 &&
            [hook[@"expected"] isEqualToString:@"554889e5817f0c122700000f94c05dc3"] &&
            [hook[@"replacement"] isEqualToString:@"31c090"];
    }
    return false;
}

static bool HexString(id value, NSUInteger length) {
    if (![value isKindOfClass:NSString.class] || [value length] != length) return false;
    for (NSUInteger i = 0; i < length; ++i) {
        const unichar c = [value characterAtIndex:i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    }
    return true;
}

// Separate installations use OS-assigned containers, independent of this plugin.
// Their explicit identity also binds the plugin plan to that one installation.
static bool BundleIdentity(NSBundle *bundle, NSString **instanceID) {
    *instanceID = nil;
    NSString *identifier = bundle.bundleIdentifier;
    if (!String(identifier)) return false;
    if ([identifier isEqualToString:@"com.tencent.xinWeChat"]) return true;
    NSString *prefix = @"local.wechattool.wechat.";
    if (![identifier hasPrefix:prefix]) return false;
    NSString *instance = [identifier substringFromIndex:prefix.length];
    id declaredInstance = [bundle objectForInfoDictionaryKey:@"WeChatToolInstanceID"];
    id source = [bundle objectForInfoDictionaryKey:@"WeChatToolSourceBundleIdentifier"];
    if (!HexString(instance, 32) || !HexString(declaredInstance, 32) ||
        ![instance isEqualToString:declaredInstance] || !String(source) ||
        ![source isEqualToString:@"com.tencent.xinWeChat"]) return false;
    *instanceID = instance;
    return true;
}

static bool IsolatedPlanIdentity(NSDictionary *plan, NSString *instanceID) {
    if (!instanceID) return true;
    return String(plan[@"source_bundle_id"]) &&
        [plan[@"source_bundle_id"] isEqualToString:@"com.tencent.xinWeChat"] &&
        HexString(plan[@"instance_id"], 32) &&
        [plan[@"instance_id"] isEqualToString:instanceID] &&
        String(plan[@"data_isolation"]) &&
        [plan[@"data_isolation"] isEqualToString:@"per-installation"];
}

static NSData *Hex(NSString *hex) {
    if (![hex isKindOfClass:NSString.class] || hex.length % 2 || !HexString(hex, hex.length)) return nil;
    NSMutableData *data = [NSMutableData dataWithLength:hex.length / 2];
    uint8_t *bytes = static_cast<uint8_t *>(data.mutableBytes);
    for (NSUInteger i = 0; i < data.length; ++i) {
        const char high = [hex characterAtIndex:i * 2];
        const char low = [hex characterAtIndex:i * 2 + 1];
        bytes[i] = ((high <= '9' ? high - '0' : high - 'a' + 10) << 4) |
                   (low <= '9' ? low - '0' : low - 'a' + 10);
    }
    return data;
}

// The plan can select a reviewed accessibility patch, never invent one. These
// profiles are embedded in the plugin and bind the entire function and image.
static NSDictionary *FindAccessibilityProfile(NSDictionary *hook, NSArray *profiles) {
    if (![hook isKindOfClass:NSDictionary.class] || ![profiles isKindOfClass:NSArray.class]) return nil;
    NSBundle *bundle = NSBundle.mainBundle;
    NSDictionary *selected = nil;
    for (NSDictionary *profile in profiles) {
        if (![profile isKindOfClass:NSDictionary.class] ||
            ![profile[@"version"] isEqual:[bundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"]] ||
            ![profile[@"build"] isEqual:[bundle objectForInfoDictionaryKey:@"CFBundleVersion"]]) continue;
        bool matches = true;
        for (NSString *key in @[@"id", @"arch", @"uuid", @"image_sha256", @"address", @"expected",
                                 @"patch_offset", @"replacement", @"function_size", @"function_sha256"])
            if (![profile[key] isEqual:hook[key]]) { matches = false; break; }
        if (matches) {
            if (selected) return nil;
            selected = profile;
        }
    }
    return selected;
}

static NSDictionary *AccessibilityProfile(NSDictionary *hook) {
    static NSArray *profiles;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        NSData *data = [NSData dataWithBytes:kCompiledAccessibilityProfiles
                                    length:sizeof(kCompiledAccessibilityProfiles) - 1];
        id document = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
        if ([document isKindOfClass:NSDictionary.class] &&
            [document[@"profiles"] isKindOfClass:NSArray.class]) profiles = document[@"profiles"];
    });
    return FindAccessibilityProfile(hook, profiles);
}

static bool AccessibilityHook(NSDictionary *hook) {
    return [hook[@"feature"] isEqual:@"accessibility"];
}

static bool AllowedHook(NSDictionary *hook) {
    if (![hook isKindOfClass:NSDictionary.class]) return false;
    if (!AccessibilityHook(hook))
        return (!hook[@"feature"] || [hook[@"feature"] isEqual:@"recall"]) && AllowedRecallHook(hook);
    uint64_t address, offset, size;
    if (!RelativeImagePath(hook[@"image"]) || hook[@"notice_adapter"] ||
        !Integer(hook[@"address"], &address) || !address || address > UINTPTR_MAX ||
        !Integer(hook[@"patch_offset"], &offset) ||
        !Integer(hook[@"function_size"], &size) || !size || size > 65536 ||
        address > UINTPTR_MAX - size || !HexString(hook[@"function_sha256"], 64) ||
        !HexString(hook[@"image_sha256"], 64) ||
        !String(hook[@"expected"], 4096) || !String(hook[@"replacement"], 32)) return false;
    NSData *expected = Hex(hook[@"expected"]), *replacement = Hex(hook[@"replacement"]);
    return expected.length && replacement.length && expected.length <= size &&
        offset <= expected.length && replacement.length <= expected.length - offset &&
        AccessibilityProfile(hook) != nil;
}

static bool SelectedFeaturesMatch(NSDictionary *plan, NSArray *hooks) {
    id value = plan[@"features"];
    // Existing plans remain recall-only; adding an accessibility hook requires
    // an explicit feature selection from the new installer/backend.
    NSArray *features = value ?: @[@"recall"];
    if (![features isKindOfClass:NSArray.class] || features.count == 0 || features.count > 2) return false;
    NSMutableSet *selected = [NSMutableSet set];
    for (id feature in features) {
        if ((![@"recall" isEqual:feature] && ![@"accessibility" isEqual:feature]) ||
            [selected containsObject:feature]) return false;
        [selected addObject:feature];
    }
    NSMutableDictionary<NSString *, NSMutableDictionary *> *counts = [NSMutableDictionary dictionary];
    for (NSDictionary *hook in hooks) {
        NSString *feature = hook[@"feature"] ?: @"recall";
        if (![selected containsObject:feature]) return false;
        NSString *arch = hook[@"arch"];
        if (!counts[arch]) counts[arch] = [NSMutableDictionary dictionary];
        counts[arch][feature] = @([counts[arch][feature] unsignedIntegerValue] + 1);
    }
    for (NSDictionary *count in counts.allValues) {
        for (NSString *feature in selected) {
            NSUInteger wanted = [feature isEqual:@"accessibility"] ? 2 : 1;
            if ([count[feature] unsignedIntegerValue] != wanted) return false;
        }
    }
    return counts.count > 0;
}

static bool Inside(uint64_t address, uint64_t length, uint64_t start, uint64_t size) {
    return address >= start && length <= size && address - start <= size - length;
}

static bool Slid(uint64_t address, intptr_t slide, uintptr_t *result) {
    if (slide >= 0) {
        if (address > UINTPTR_MAX - static_cast<uintptr_t>(slide)) return false;
        *result = static_cast<uintptr_t>(address) + static_cast<uintptr_t>(slide);
    } else {
        const uintptr_t magnitude = static_cast<uintptr_t>(-(slide + 1)) + 1;
        if (address < magnitude || address - magnitude > UINTPTR_MAX) return false;
        *result = static_cast<uintptr_t>(address - magnitude);
    }
    return true;
}

// Only dyld-provided headers reach this function, never a plan-supplied pointer.
static bool ValidateImage(const mach_header *header, NSDictionary *hook, size_t length) {
    if (header->magic != MH_MAGIC_64) return false;
#if defined(__arm64__)
    if (header->cputype != CPU_TYPE_ARM64) return false;
#else
    if (header->cputype != CPU_TYPE_X86_64) return false;
#endif
    const auto *header64 = reinterpret_cast<const mach_header_64 *>(header);
    if (header64->ncmds > 4096 || header64->sizeofcmds > 16 * 1024 * 1024) return false;
    const uint8_t *cursor = reinterpret_cast<const uint8_t *>(header64 + 1);
    size_t remaining = header64->sizeofcmds;
    bool uuidMatches = false, textContains = false;
    uint64_t address = [hook[@"address"] unsignedLongLongValue];
    for (uint32_t index = 0; index < header64->ncmds; ++index) {
        if (remaining < sizeof(load_command)) return false;
        const auto *command = reinterpret_cast<const load_command *>(cursor);
        if (command->cmdsize < sizeof(load_command) || command->cmdsize > remaining)
            return false;
        if (command->cmd == LC_UUID) {
            if (command->cmdsize != sizeof(uuid_command)) return false;
            uuid_string_t value;
            uuid_unparse_lower(reinterpret_cast<const uuid_command *>(cursor)->uuid, value);
            if ([hook[@"uuid"] isEqualToString:@(value)]) uuidMatches = true;
        } else if (command->cmd == LC_SEGMENT_64) {
            if (command->cmdsize < sizeof(segment_command_64)) return false;
            const auto *segment = reinterpret_cast<const segment_command_64 *>(cursor);
            if (segment->nsects > (command->cmdsize - sizeof(*segment)) / sizeof(section_64))
                return false;
            if (!strncmp(segment->segname, "__TEXT", 16) &&
                (segment->initprot & (VM_PROT_READ | VM_PROT_EXECUTE)) ==
                    (VM_PROT_READ | VM_PROT_EXECUTE)) {
                const auto *sections = reinterpret_cast<const section_64 *>(segment + 1);
                for (uint32_t s = 0; s < segment->nsects; ++s) {
                    const auto &section = sections[s];
                    if (!strncmp(section.segname, "__TEXT", 16) &&
                        !strncmp(section.sectname, "__text", 16) &&
                        (section.flags & S_ATTR_PURE_INSTRUCTIONS) &&
                        Inside(section.addr, section.size, segment->vmaddr, segment->vmsize) &&
                        Inside(address, length, section.addr, section.size)) textContains = true;
                }
            }
        }
        cursor += command->cmdsize;
        remaining -= command->cmdsize;
    }
    return remaining == 0 && uuidMatches && textContains;
}

struct ProtectedPage {
    mach_vm_address_t start;
    mach_vm_size_t size;
    vm_prot_t protection;
    bool writable;
};

static bool PageProtection(mach_vm_address_t start, mach_vm_size_t size, vm_prot_t *protection) {
    mach_vm_address_t region = start;
    mach_vm_size_t regionSize = 0;
    vm_region_basic_info_data_64_t info = {};
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    kern_return_t result = mach_vm_region(mach_task_self(), &region, &regionSize,
        VM_REGION_BASIC_INFO_64, reinterpret_cast<vm_region_info_t>(&info), &count, &object);
    if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
    if (result != KERN_SUCCESS || !Inside(start, size, region, regionSize) ||
        (info.protection & (VM_PROT_READ | VM_PROT_EXECUTE)) !=
            (VM_PROT_READ | VM_PROT_EXECUTE)) return false;
    *protection = info.protection;
    return true;
}

static bool Restore(ProtectedPage *pages, size_t count) {
    bool restored = true;
    for (size_t i = 0; i < count; ++i) {
        if (!pages[i].writable) continue;
        const kern_return_t result = mach_vm_protect(mach_task_self(), pages[i].start,
            pages[i].size, false, pages[i].protection);
        if (result != KERN_SUCCESS) restored = false;
        else pages[i].writable = false;
    }
    return restored;
}

static NSString *Patch(uintptr_t address, NSData *expected, NSUInteger offset, NSData *replacement) {
    const vm_size_t pageSize = vm_page_size;
    if (!pageSize || (pageSize & (pageSize - 1)) ||
        address > UINTPTR_MAX - expected.length ||
        offset > expected.length || replacement.length > expected.length - offset)
        return @"invalid-range";
    const uintptr_t first = address & ~(static_cast<uintptr_t>(pageSize) - 1);
    const uintptr_t last = (address + expected.length - 1) & ~(static_cast<uintptr_t>(pageSize) - 1);
    // Every allowed signature is shorter than a page and can overlap at most two.
    ProtectedPage pages[2] = {};
    const size_t count = first == last ? 1 : 2;
    for (size_t i = 0; i < count; ++i) {
        pages[i] = {first + i * pageSize, pageSize, VM_PROT_NONE, false};
        if (!PageProtection(pages[i].start, pages[i].size, &pages[i].protection))
            return @"unreadable-or-nonexecutable-page";
    }
    auto *target = reinterpret_cast<uint8_t *>(address);
    NSMutableData *patched = [expected mutableCopy];
    memcpy(static_cast<uint8_t *>(patched.mutableBytes) + offset,
           replacement.bytes, replacement.length);
    if (memcmp(target, patched.bytes, patched.length) == 0) return @"already-active";
    if (memcmp(target, expected.bytes, expected.length) != 0) return @"bytes-mismatch";

    for (size_t i = 0; i < count; ++i) {
        // COW keeps the on-disk binary untouched. Never request RWX memory.
        const kern_return_t result = mach_vm_protect(mach_task_self(), pages[i].start,
            pages[i].size, false, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY);
        if (result != KERN_SUCCESS) {
            if (!Restore(pages, count)) return @"protection-restore-failed";
            return @"write-protection-denied";
        }
        pages[i].writable = true;
    }
    if (memcmp(target, expected.bytes, expected.length) != 0) {
        if (!Restore(pages, count)) return @"protection-restore-failed";
        return @"bytes-changed-before-write";
    }
#if defined(__arm64__)
    if (replacement.length == sizeof(uint32_t)) {
        uint32_t instruction;
        memcpy(&instruction, replacement.bytes, sizeof(instruction));
        __atomic_store_n(reinterpret_cast<uint32_t *>(target + offset), instruction, __ATOMIC_RELEASE);
    } else {
        // The notice branch is installed only in a fresh dyld image, before
        // initializers or message-processing threads can execute this code.
        memcpy(target + offset, replacement.bytes, replacement.length);
    }
#else
    // The callback refuses images already loaded before this module started.
    memcpy(target + offset, replacement.bytes, replacement.length);
#endif
    sys_icache_invalidate(target + offset, replacement.length);
    const bool verified = memcmp(target, patched.bytes, patched.length) == 0;
    if (!Restore(pages, count)) return @"protection-restore-failed";
    return verified ? @"active" : @"write-verification-failed";
}

// Only reviewed layouts compiled into this plugin can enable metadata reads.
// An app plan selects an adapter by ID; it cannot supply offsets or callbacks.
static bool ValidNoticeProfile(id value) {
    if (![value isKindOfClass:NSDictionary.class]) return false;
    NSDictionary *profile = value;
    if (!String(profile[@"id"], 128) || !String(profile[@"version"], 64) ||
        !String(profile[@"build"], 64) || !String(profile[@"arch"], 16) ||
        !String(profile[@"layout"], 128) || !String(profile[@"uuid"], 36) ||
        !HexString(profile[@"image_sha256"], 64)) return false;
    const bool arm = [profile[@"arch"] isEqualToString:@"arm64"];
    if (!arm && ![profile[@"arch"] isEqualToString:@"x86_64"]) return false;
    if (![profile[@"layout"] isEqualToString:arm ? @"messagewrap-libcpp-alt-0x130" :
                                                  @"messagewrap-libcpp-default-0x130"]) return false;
    uuid_t parsed;
    uuid_string_t normalized;
    if (uuid_parse([profile[@"uuid"] UTF8String], parsed) != 0) return false;
    uuid_unparse_lower(parsed, normalized);
    if (![profile[@"uuid"] isEqualToString:@(normalized)]) return false;
    uint64_t predicate;
    if (!Integer(profile[@"predicate_address"], &predicate) || !predicate || predicate > UINT64_MAX - 20 ||
        (arm && predicate % 4)) return false;
    for (NSString *key in @[@"insert_notice", @"handler", @"task_slot"]) {
        id probeValue = profile[key];
        if (![probeValue isKindOfClass:NSDictionary.class]) return false;
        NSDictionary *probe = probeValue;
        uint64_t address, size;
        if (!Integer(probe[@"address"], &address) || !address ||
            !Integer(probe[@"size"], &size) || !size || size > 64 * 1024 ||
            address > UINT64_MAX - size || (arm && address % 4) ||
            !HexString(probe[@"sha256"], 64)) return false;
        if ([key isEqualToString:@"handler"] &&
            (size < 32 || !HexString(probe[@"expected"], 64))) return false;
    }
    return true;
}

static NSArray *DecodeNoticeProfiles(NSData *data) {
    id document = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![document isKindOfClass:NSDictionary.class]) return nil;
    uint64_t schema;
    id profiles = document[@"adapters"];
    if (!Integer(document[@"schema_version"], &schema) || schema != 1 ||
        ![profiles isKindOfClass:NSArray.class]) return nil;
    for (id profile in profiles) if (!ValidNoticeProfile(profile)) return nil;
    return profiles;
}

static NSDictionary *FindNoticeAdapter(NSDictionary *hook, NSArray *profiles) {
    NSBundle *bundle = NSBundle.mainBundle;
    NSDictionary *selected = nil;
    for (NSDictionary *profile in profiles) {
        if ([profile[@"layout"] isEqual:kNoticeLayout] &&
            [profile[@"arch"] isEqual:kArchitecture] &&
            [profile[@"uuid"] isEqual:hook[@"uuid"]] &&
            [profile[@"image_sha256"] isEqual:hook[@"image_sha256"]] &&
            [profile[@"predicate_address"] isEqual:hook[@"address"]] &&
            [profile[@"version"] isEqual:[bundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"]] &&
            [profile[@"build"] isEqual:[bundle objectForInfoDictionaryKey:@"CFBundleVersion"]]) {
            // Match identity before ID, exactly as the Python selector does.
            if (selected) return nil;
            selected = profile;
        }
    }
    return [selected[@"id"] isEqual:hook[@"notice_adapter"]] ? selected : nil;
}

static NSDictionary *NoticeAdapter(NSDictionary *hook) {
    if (!String(hook[@"notice_adapter"], 128)) return nil;
    static NSArray *profiles;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        NSData *data = [NSData dataWithBytes:kCompiledNoticeProfiles length:sizeof(kCompiledNoticeProfiles) - 1];
        profiles = DecodeNoticeProfiles(data);
    });
    return FindNoticeAdapter(hook, profiles);
}

static bool NoticeFunction(const mach_header *header, intptr_t slide, NSDictionary *hook,
                           NSDictionary *probe, uintptr_t *runtimeAddress) {
    const uint64_t size = [probe[@"size"] unsignedLongLongValue];
    NSMutableDictionary *region = [hook mutableCopy];
    region[@"address"] = probe[@"address"];
    if (!ValidateImage(header, region, static_cast<size_t>(size)) ||
        !Slid([probe[@"address"] unsignedLongLongValue], slide, runtimeAddress)) return false;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(reinterpret_cast<const void *>(*runtimeAddress), static_cast<CC_LONG>(size), digest);
    return [[NSData dataWithBytes:digest length:sizeof(digest)] isEqualToData:Hex(probe[@"sha256"])];
}

static NSString *TryNoticeHook(const mach_header *header, intptr_t slide, NSDictionary *hook) {
    NSDictionary *adapter = NoticeAdapter(hook);
    if (!adapter) return @"recall-handler-unavailable";
    const char *disabled = getenv("WECHATTOOL_NOTICES");
    const bool noticesEnabled = !(disabled && strcmp(disabled, "0") == 0);
    uintptr_t handler, emitter, taskSlot;
    if (!NoticeFunction(header, slide, hook, adapter[@"handler"], &handler) ||
        !NoticeFunction(header, slide, hook, adapter[@"insert_notice"], &emitter) ||
        !NoticeFunction(header, slide, hook, adapter[@"task_slot"], &taskSlot))
        return @"recall-notices-function-mismatch";
    const bool chinese = [NSLocale.preferredLanguages.firstObject hasPrefix:@"zh"];
    // The intercepted handler already executes inside a native WeChat task.
    // Never call its yielding message services from a Cocoa/GCD callback.
    if (noticesEnabled)
        WCTConfigureRecallNotices(reinterpret_cast<WCTRecallEmitter>(emitter),
                                 reinterpret_cast<WCTTaskSlotGetter>(taskSlot), chinese);
    const uintptr_t callback = reinterpret_cast<uintptr_t>(&WCTHandleRecallMessage);
#if defined(__arm64__)
    // LDR X16, literal; BR X16; 64-bit callback. No displaced code is executed.
    uint8_t jump[16] = {0x50, 0x00, 0x00, 0x58, 0x00, 0x02, 0x1f, 0xd6};
    memcpy(jump + 8, &callback, sizeof(callback));
#else
    // JMP [RIP+0]; 64-bit callback. Preserve all incoming argument registers.
    uint8_t jump[14] = {0xff, 0x25, 0x00, 0x00, 0x00, 0x00};
    memcpy(jump + 6, &callback, sizeof(callback));
#endif
    return Patch(handler, Hex(adapter[@"handler"][@"expected"]), 0,
                 [NSData dataWithBytes:jump length:sizeof(jump)]);
}

static NSData *ImageDigest(NSString *path) {
    // Avoid initializing stream classes (which may load another image) while
    // inside the dyld callback and holding the runtime mutex.
    const int descriptor = open(path.fileSystemRepresentation, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) return nil;
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    uint8_t buffer[32768];
    ssize_t length;
    while (true) {
        length = read(descriptor, buffer, sizeof(buffer));
        if (length < 0 && errno == EINTR) continue;
        if (length <= 0) break;
        CC_SHA256_Update(&context, buffer, static_cast<CC_LONG>(length));
    }
    close(descriptor);
    if (length < 0) return nil;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    return [NSData dataWithBytes:digest length:sizeof(digest)];
}

static bool AccessibilityGroupMatches(const mach_header *header, intptr_t slide,
                                      NSString *actual, NSDictionary *first, NSArray *hooks) {
    if (![ImageDigest(actual) isEqual:Hex(first[@"image_sha256"])]) return false;
    NSUInteger count = 0;
    // Preflight both functions before changing either. One stale or altered
    // function refuses the whole accessibility feature for this image.
    for (NSDictionary *hook in hooks) {
        if (!AccessibilityHook(hook) || ![hook[@"arch"] isEqual:kArchitecture] ||
            ![hook[@"image"] isEqual:first[@"image"]]) continue;
        NSDictionary *profile = AccessibilityProfile(hook);
        uintptr_t address;
        const NSUInteger size = [profile[@"function_size"] unsignedIntegerValue];
        if (!profile || ![hook[@"image_sha256"] isEqual:first[@"image_sha256"]] ||
            !ValidateImage(header, hook, size) || !Slid([hook[@"address"] unsignedLongLongValue], slide, &address))
            return false;
        unsigned char digest[CC_SHA256_DIGEST_LENGTH];
        CC_SHA256(reinterpret_cast<const void *>(address), static_cast<CC_LONG>(size), digest);
        if (![[NSData dataWithBytes:digest length:sizeof(digest)] isEqual:Hex(profile[@"function_sha256"])])
            return false;
        count++;
    }
    return count == 2;
}

static void ImageAdded(const mach_header *header, intptr_t slide) {
    @autoreleasepool {
        Runtime *runtime = gRuntime;
        if (!runtime) return;
        Dl_info information = {};
        if (!dladdr(header, &information) || !information.dli_fname) return;
        NSString *actual = Canonical(@(information.dli_fname));
        if (!actual) return;
        pthread_mutex_lock(&runtime->mutex);
        bool accessibilityChecked = false, accessibilityMatches = false;
        for (NSDictionary *hook in runtime->hooks) {
            if (![hook[@"arch"] isEqualToString:kArchitecture]) continue;
            NSString *key = [NSString stringWithFormat:@"%@:%@:%@", hook[@"arch"],
                             hook[@"image"], hook[@"address"]];
            if ([runtime->finished containsObject:key]) continue;
            NSString *expectedPath = [runtime->root stringByAppendingPathComponent:hook[@"image"]];
            NSString *canonical = Canonical(expectedPath);
            if (!canonical || ![canonical hasPrefix:[runtime->root stringByAppendingString:@"/"]] ||
                ![actual isEqualToString:canonical]) continue;
            [runtime->finished addObject:key];
            if ([runtime->initialImages containsObject:[NSValue valueWithPointer:header]]) {
                // Even an atomic ARM store needs a temporary nonexecutable COW
                // page. Only patch fresh dyld images before their initializers.
                Log(runtime->log, @"late-image-refused", hook[@"id"]);
                continue;
            }
            if (AccessibilityHook(hook)) {
                if (!accessibilityChecked) {
                    accessibilityMatches = AccessibilityGroupMatches(header, slide, actual, hook, runtime->hooks);
                    accessibilityChecked = true;
                }
                if (!accessibilityMatches) {
                    Log(runtime->log, @"accessibility-profile-mismatch", hook[@"id"]);
                    continue;
                }
            }
            NSData *expected = Hex(hook[@"expected"]);
            if (!ValidateImage(header, hook, expected.length)) {
                Log(runtime->log, @"uuid-or-text-range-mismatch", hook[@"id"]);
                continue;
            }
            uintptr_t address;
            if (!Slid([hook[@"address"] unsignedLongLongValue], slide, &address)) {
                Log(runtime->log, @"invalid-slide", hook[@"id"]);
                continue;
            }
            // The global classifier also constructs OUTGOING recall extensions.
            // Forcing it false makes own-message Recall dereference a null extension.
            // Preserve classification and intercept only the reviewed receive handler.
            // Never fall back to the unsafe predicate patch if its profile is absent.
            NSString *status = AccessibilityHook(hook)
                ? Patch(address, expected, [hook[@"patch_offset"] unsignedIntegerValue], Hex(hook[@"replacement"]))
                : TryNoticeHook(header, slide, hook);
            const bool active = [status isEqualToString:@"active"] || [status isEqualToString:@"already-active"];
            if (active)
                runtime->applied++;
            Log(runtime->log, status, hook[@"id"]);
            if (active && !AccessibilityHook(hook))
                Log(runtime->log, @"recall-handler-active", hook[@"id"]);
        }
        pthread_mutex_unlock(&runtime->mutex);
    }
}

__attribute__((constructor)) static void Start() {
    @autoreleasepool {
        os_log_t logger = os_log_create("local.wechattool", "runtime");
        NSBundle *bundle = NSBundle.mainBundle;
        NSString *root = Canonical(bundle.bundlePath);
        NSString *instanceID = nil;
        if (!root || !BundleIdentity(bundle, &instanceID) ||
            ![Canonical(bundle.executablePath) isEqualToString:
                [root stringByAppendingPathComponent:@"Contents/MacOS/WeChat"]]) return;
        if (instanceID && !IsolateShareNotifications()) {
            Log(logger, @"isolation-unavailable", @"share-routing");
            _exit(78);
        }
        NSString *resources = [root stringByAppendingPathComponent:@"Contents/Resources/WeChatTool"];
        const char *disabled = getenv("WECHATTOOL_DISABLE");
        if ((disabled && strcmp(disabled, "1") == 0) ||
            [[NSFileManager defaultManager] fileExistsAtPath:
                [resources stringByAppendingPathComponent:@"disabled"]]) {
            Log(logger, @"disabled", @"-");
            return;
        }
        NSData *data = [NSData dataWithContentsOfFile:[resources stringByAppendingPathComponent:@"plan.json"]
                                             options:NSDataReadingUncached error:nil];
        if (!data || data.length > 128 * 1024) {
            Log(logger, @"missing-or-oversized-plan", @"-");
            return;
        }
        id plan = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
        if (![plan isKindOfClass:NSDictionary.class]) {
            Log(logger, @"invalid-plan", @"-");
            return;
        }
        uint64_t schema;
        if (!Integer(plan[@"schema_version"], &schema) || schema != 1 ||
            !String(plan[@"bundle_id"]) || !String(plan[@"version"], 64) || !String(plan[@"build"], 64) ||
            ![plan[@"bundle_id"] isEqualToString:bundle.bundleIdentifier] ||
            !IsolatedPlanIdentity(plan, instanceID) ||
            ![plan[@"version"] isEqualToString:[bundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"]] ||
            ![plan[@"build"] isEqualToString:[bundle objectForInfoDictionaryKey:@"CFBundleVersion"]] ||
            (plan[@"enabled"] && !Boolean(plan[@"enabled"]))) {
            Log(logger, @"plan-identity-mismatch", @"-");
            return;
        }
        if (plan[@"enabled"] && ![plan[@"enabled"] boolValue]) {
            Log(logger, @"disabled", @"-");
            return;
        }
        NSArray *hooks = plan[@"hooks"];
        if (![hooks isKindOfClass:NSArray.class] || hooks.count == 0 || hooks.count > 128) {
            Log(logger, @"invalid-hooks", @"-");
            return;
        }
        NSMutableSet *keys = [NSMutableSet set];
        bool relevant = false;
        for (NSDictionary *hook in hooks) {
            if (!AllowedHook(hook)) {
                Log(logger, @"unsupported-hook", @"-");
                return;
            }
            NSString *key = [NSString stringWithFormat:@"%@:%@:%@", hook[@"arch"], hook[@"image"], hook[@"address"]];
            if ([keys containsObject:key]) {
                Log(logger, @"duplicate-hook", @"-");
                return;
            }
            [keys addObject:key];
            if ([hook[@"arch"] isEqualToString:kArchitecture]) relevant = true;
        }
        if (!SelectedFeaturesMatch(plan, hooks)) {
            Log(logger, @"feature-selection-mismatch", @"-");
            return;
        }
        if (!relevant) {
            Log(logger, @"no-hook-for-architecture", @"-");
            return;
        }
        NSMutableSet *initial = [NSMutableSet set];
        for (uint32_t i = 0; i < _dyld_image_count(); ++i)
            [initial addObject:[NSValue valueWithPointer:_dyld_get_image_header(i)]];
        Runtime *runtime = new Runtime;
        runtime->root = root;
        runtime->hooks = hooks;
        runtime->initialImages = initial;
        runtime->finished = [NSMutableSet set];
        runtime->log = logger;
        gRuntime = runtime;
        _dyld_register_func_for_add_image(ImageAdded);
        pthread_mutex_lock(&runtime->mutex);
        const bool active = runtime->applied > 0;
        const bool matched = runtime->finished.count > 0;
        pthread_mutex_unlock(&runtime->mutex);
        Log(logger, active ? @"initialized-active" :
            (matched ? @"initialized-refused" : @"initialized-no-image-matched"), @"-");
    }
}

} // namespace
