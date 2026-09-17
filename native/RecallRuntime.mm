// SPDX-License-Identifier: MIT
#import "RecallRuntime.h"
#import "RecallNotice.h"
#import <Foundation/Foundation.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstring>
#include <deque>
#include <limits>
#include <mutex>

namespace {

constexpr size_t kStringSize = 24;
constexpr size_t kMaximumStringBytes = 64 * 1024;
constexpr size_t kMaximumNotices = 512;
constexpr uintptr_t kTypeOffset = 12;
constexpr uintptr_t kSessionOffset = 24;
constexpr uintptr_t kXMLOffset = 304;
constexpr uint32_t kRevokeMessageType = 10002;
static_assert(sizeof(std::string) == kStringSize, "The verified adapter uses Apple's 24-byte libc++ string ABI");
static_assert(sizeof(uintptr_t) == 8, "The verified adapter requires a 64-bit process");

struct Entry {
    std::string key;
    bool pending;
    uintptr_t context;
    uintptr_t control;
};

// Process-lifetime state is intentional: installed handler detours never unload.
struct Runtime {
    std::mutex mutex;
    std::deque<Entry> entries;
    WCTTaskSlotGetter taskSlotGetter = nullptr;
    bool chinese = false;
};

static std::atomic<WCTRecallEmitter> gEmitter{nullptr};
static Runtime *gRuntime = nullptr;
static std::mutex gConfigurationMutex;

static uintptr_t Address(const void *base, uintptr_t offset) {
    const uintptr_t address = reinterpret_cast<uintptr_t>(base);
    return address && offset <= std::numeric_limits<uintptr_t>::max() - address ? address + offset : 0;
}

static bool Read(uintptr_t address, void *destination, size_t size) {
    if (!address || !destination || !size || size > kMaximumStringBytes ||
        size - 1 > std::numeric_limits<uintptr_t>::max() - address)
        return false;
    mach_vm_size_t copied = 0;
    const kern_return_t result = mach_vm_read_overwrite(
        mach_task_self(), static_cast<mach_vm_address_t>(address), size,
        reinterpret_cast<mach_vm_address_t>(destination), &copied);
    return result == KERN_SUCCESS && copied == size;
}

static bool SnapshotString(uintptr_t address, std::string &result) {
    std::array<uint8_t, kStringSize> layout{}, after{};
    if (!Read(address, layout.data(), layout.size())) return false;
#if defined(__arm64__)
    // Apple Silicon libc++ uses its alternate layout: inline bytes first,
    // short length/long discriminator last; the long pointer is the first word.
    const bool isLong = layout.back() >= 0x80;
    const size_t shortLength = layout.back();
    constexpr size_t inlineOffset = 0, pointerOffset = 0, capacityOffset = 16;
    constexpr uint64_t capacityMask = UINT64_C(0x7fffffffffffffff);
#elif defined(__x86_64__)
    // Intel libc++ uses the original layout: discriminator/short length first,
    // inline bytes at +1; long capacity, size and pointer occupy the three words.
    const bool isLong = (layout.front() & 1) != 0;
    const size_t shortLength = layout.front() >> 1;
    constexpr size_t inlineOffset = 1, pointerOffset = 16, capacityOffset = 0;
    constexpr uint64_t capacityMask = UINT64_C(0xfffffffffffffffe);
#else
#error Unsupported native string layout
#endif
    if (!isLong) {
        if (!shortLength || shortLength > 22 || layout[inlineOffset + shortLength] != 0) return false;
        result.assign(reinterpret_cast<const char *>(layout.data() + inlineOffset), shortLength);
    } else {
        uint64_t pointer = 0, length = 0, capacity = 0;
        std::memcpy(&pointer, layout.data() + pointerOffset, sizeof(pointer));
        std::memcpy(&length, layout.data() + 8, sizeof(length));
        std::memcpy(&capacity, layout.data() + capacityOffset, sizeof(capacity));
        capacity &= capacityMask;
        if (!length || length > kMaximumStringBytes || capacity <= length) return false;
        result.resize(static_cast<size_t>(length));
        if (!Read(static_cast<uintptr_t>(pointer), result.data(), result.size())) return false;
    }
    // Metadata must remain stable across the guarded copy. No source pointer is
    // retained after returning; emission owns independent string snapshots.
    return Read(address, after.data(), after.size()) && layout == after;
}

struct Context {
    uintptr_t object;
    uintptr_t control;
};

static bool CurrentContext(WCTTaskSlotGetter getter, Context &context) {
    const uintptr_t slot = reinterpret_cast<uintptr_t>(getter());
    uintptr_t holder = 0, afterHolder = 0;
    std::array<uintptr_t, 2> weak{}, afterWeak{};
    if (!Read(slot, &holder, sizeof(holder)) || !holder ||
        !Read(holder, weak.data(), sizeof(weak)) || !weak[0] || !weak[1]) return false;
    int64_t owners = -1;
    uint32_t mode = 0, guardCount = 0;
#if defined(__arm64__)
    constexpr uintptr_t modeOffset = 0x288, guardCountOffset = 0x3c8;
#elif defined(__x86_64__)
    constexpr uintptr_t modeOffset = 0x188, guardCountOffset = 0x2c8;
#endif
    if (!Read(Address(reinterpret_cast<const void *>(weak[1]), 8), &owners, sizeof(owners)) || owners < 0 ||
        !Read(Address(reinterpret_cast<const void *>(weak[0]), modeOffset), &mode, sizeof(mode)) ||
        !Read(Address(reinterpret_cast<const void *>(weak[0]), guardCountOffset), &guardCount, sizeof(guardCount)) ||
        !Read(slot, &afterHolder, sizeof(afterHolder)) || holder != afterHolder ||
        !Read(holder, afterWeak.data(), sizeof(afterWeak)) || weak != afterWeak) return false;
    // Do not invent an enum restriction or manipulate private shared_ptr state.
    // These reads are a refusal check, not a substitute for the reviewed native
    // handler's borrowed task lifetime. The native emitter acquires its own guard.
    context = {weak[0], weak[1]};
    return true;
}

static bool Reserve(Runtime *runtime, const std::string &key, Context context) {
    std::lock_guard<std::mutex> lock(runtime->mutex);
    auto found = std::find_if(runtime->entries.begin(), runtime->entries.end(),
                              [&](const Entry &entry) {
        return entry.key == key || (entry.pending && entry.context == context.object && entry.control == context.control);
    });
    if (found != runtime->entries.end()) return false;
    if (runtime->entries.size() == kMaximumNotices) {
        auto completed = std::find_if(runtime->entries.begin(), runtime->entries.end(),
                                      [](const Entry &entry) { return !entry.pending; });
        if (completed == runtime->entries.end()) return false;
        runtime->entries.erase(completed);
    }
    runtime->entries.push_back({key, true, context.object, context.control});
    return true;
}

static void Complete(Runtime *runtime, const std::string &key, Context context, bool success) noexcept {
    try {
        std::lock_guard<std::mutex> lock(runtime->mutex);
        for (auto entry = runtime->entries.begin(); entry != runtime->entries.end(); ++entry) {
            if (entry->key == key && entry->pending && entry->context == context.object && entry->control == context.control) {
                if (success) {
                    entry->pending = false;
                    entry->context = entry->control = 0;
                } else {
                    runtime->entries.erase(entry);
                }
                break;
            }
        }
    } catch (...) {
        // A notice failure can never change message preservation behavior.
    }
}

static bool UTF8(NSString *value, std::string &result) {
    if (![value isKindOfClass:NSString.class] || !value.length) return false;
    NSData *encoded = [value dataUsingEncoding:NSUTF8StringEncoding allowLossyConversion:NO];
    if (!encoded.length || encoded.length > kMaximumStringBytes) return false;
    result.assign(static_cast<const char *>(encoded.bytes), encoded.length);
    return result.find('\0') == std::string::npos;
}

}  // namespace

void WCTConfigureRecallNotices(WCTRecallEmitter emitter, WCTTaskSlotGetter taskSlotGetter, bool chinese) noexcept {
    if (!emitter || !taskSlotGetter) return;
    try {
        @try {
            std::lock_guard<std::mutex> lock(gConfigurationMutex);
            if (gEmitter.load(std::memory_order_relaxed)) return;
            Runtime *runtime = new Runtime;
            runtime->taskSlotGetter = taskSlotGetter;
            runtime->chinese = chinese;
            gRuntime = runtime;
            gEmitter.store(emitter, std::memory_order_release);
        } @catch (__unused NSException *exception) {
        }
    } catch (...) {
    }
}

extern "C" bool WCTHandleRecallMessage(const void *service, const void *message) noexcept {
    (void)service;
    const WCTRecallEmitter emitter = gEmitter.load(std::memory_order_acquire);
    if (!emitter) return true;
    try {
        @try {
            Runtime *runtime = gRuntime;
            std::string session, messageID, text;
            @autoreleasepool {
                uint32_t type = 0;
                if (!Read(Address(message, kTypeOffset), &type, sizeof(type)) || type != kRevokeMessageType)
                    return true;
                std::string xml, recordSession;
                if (!SnapshotString(Address(message, kXMLOffset), xml) ||
                    !SnapshotString(Address(message, kSessionOffset), recordSession))
                    return true;
                uint32_t afterType = 0;
                if (!Read(Address(message, kTypeOffset), &afterType, sizeof(afterType)) || afterType != type)
                    return true;
                NSString *sessionValue = [[NSString alloc] initWithBytes:recordSession.data()
                                                                 length:recordSession.size()
                                                               encoding:NSUTF8StringEncoding];
                if (!sessionValue.length || recordSession.find('\0') != std::string::npos) return true;
                NSData *data = [NSData dataWithBytes:xml.data() length:xml.size()];
                NSDictionary<NSString *, NSString *> *notice = WCTParseRecallNotice(data, runtime->chinese);
                if (!notice || ![notice[@"session"] isEqualToString:sessionValue]) return true;
                if (!UTF8(notice[@"session"], session) || !UTF8(notice[@"messageID"], messageID) ||
                    !UTF8(notice[@"text"], text))
                    return true;
            }
            const std::string key = session + '\0' + messageID;
            Context context{};
            if (!CurrentContext(runtime->taskSlotGetter, context) || !Reserve(runtime, key, context)) return true;
            bool success = false;
            // No mutex, Objective-C pool, or thread-local recursion flag spans
            // this call. The native helper may yield its coroutine; another
            // task can use this OS thread before this task resumes elsewhere.
            try {
                @try {
                    emitter(0, &session, &text);
                    success = true;
                } @catch (__unused NSException *exception) {
                }
            } catch (...) {
            }
            Complete(runtime, key, context, success);
        } @catch (__unused NSException *exception) {
        }
    } catch (...) {
    }
    return true;
}
