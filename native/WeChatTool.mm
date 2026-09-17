// SPDX-License-Identifier: MIT
// Original implementation. This module never reads messages or account data.
#import <Foundation/Foundation.h>
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <dlfcn.h>
#include <os/log.h>
#include <pthread.h>
#include <uuid/uuid.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

namespace {

#if defined(__arm64__)
static NSString *const kArchitecture = @"arm64";
#elif defined(__x86_64__)
static NSString *const kArchitecture = @"x86_64";
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

// These are the only instruction replacements implemented by this release.
// A plan selects locations; it cannot supply arbitrary machine code.
static bool AllowedHook(NSDictionary *hook) {
    if (![hook isKindOfClass:NSDictionary.class] || !String(hook[@"id"], 128) ||
        !RelativeImagePath(hook[@"image"]) || !String(hook[@"uuid"], 36) ||
        !String(hook[@"arch"], 16) || !String(hook[@"expected"], 128) ||
        !String(hook[@"replacement"], 16)) return false;
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

static NSData *Hex(NSString *hex) {
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
    uint32_t instruction;
    memcpy(&instruction, replacement.bytes, sizeof(instruction));
    __atomic_store_n(reinterpret_cast<uint32_t *>(target + offset), instruction, __ATOMIC_RELEASE);
#else
    // The callback refuses images already loaded before this module started.
    memcpy(target + offset, replacement.bytes, replacement.length);
#endif
    sys_icache_invalidate(target + offset, replacement.length);
    const bool verified = memcmp(target, patched.bytes, patched.length) == 0;
    if (!Restore(pages, count)) return @"protection-restore-failed";
    return verified ? @"active" : @"write-verification-failed";
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
            NSString *status = Patch(address, expected, [hook[@"patch_offset"] unsignedIntegerValue],
                                     Hex(hook[@"replacement"]));
            if ([status isEqualToString:@"active"] || [status isEqualToString:@"already-active"])
                runtime->applied++;
            Log(runtime->log, status, hook[@"id"]);
        }
        pthread_mutex_unlock(&runtime->mutex);
    }
}

__attribute__((constructor)) static void Start() {
    @autoreleasepool {
        os_log_t logger = os_log_create("local.wechattool", "runtime");
        NSBundle *bundle = NSBundle.mainBundle;
        NSString *root = Canonical(bundle.bundlePath);
        if (!root || ![bundle.bundleIdentifier isEqualToString:@"com.tencent.xinWeChat"] ||
            ![Canonical(bundle.executablePath) isEqualToString:
                [root stringByAppendingPathComponent:@"Contents/MacOS/WeChat"]]) return;
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
