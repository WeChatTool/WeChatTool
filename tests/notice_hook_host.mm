// SPDX-License-Identifier: MIT
// Headless fixture and host. Never loads WeChat or accesses chat storage.
#import <Foundation/Foundation.h>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <string>

#if defined(WCT_NOTICE_FIXTURE)

static unsigned originalCalls = 0, emittedCount = 0;
static std::string emittedSession, emittedText;
static bool contextAvailable = true;
static std::array<uint8_t, 1024> task{};
static std::array<int64_t, 3> control{{0, 0, 0}};
static std::array<uintptr_t, 2> holder{{reinterpret_cast<uintptr_t>(task.data()),
                                       reinterpret_cast<uintptr_t>(control.data())}};
static thread_local uintptr_t slot = 0;

extern "C" __attribute__((visibility("default"), noinline))
bool original_notice_handler(const void *, const void *) { ++originalCalls; return true; }

// A distinct, adequately sized native handler whose original implementation is
// observable. The real preservation predicate remains in native_fixture.S.
#if defined(__arm64__)
asm(".text\n.p2align 2\n.globl _notice_handler\n_notice_handler:\n"
    ".rept 16\n nop\n.endr\n b _original_notice_handler\n");
#else
asm(".text\n.p2align 4\n.globl _notice_handler\n_notice_handler:\n"
    ".rept 64\n nop\n.endr\n jmp _original_notice_handler\n");
#endif

extern "C" __attribute__((visibility("default"), noinline))
void insert_notice(uintptr_t unused, const std::string *session, const std::string *text) {
    if (unused || !session || !text) std::abort();
    emittedSession = *session;
    emittedText = *text;
    ++emittedCount;
}
extern "C" __attribute__((visibility("default"), noinline))
const void *task_slot() { slot = contextAvailable ? reinterpret_cast<uintptr_t>(holder.data()) : 0; return &slot; }
extern "C" unsigned original_calls() { return originalCalls; }
extern "C" unsigned emitted_count() { return emittedCount; }
extern "C" const char *emitted_session() { return emittedSession.c_str(); }
extern "C" const char *emitted_text() { return emittedText.c_str(); }
extern "C" void set_context(bool available) { contextAvailable = available; }

#else

static void Require(bool condition, const char *message) {
    if (!condition) { std::fprintf(stderr, "FAIL: %s\n", message); std::exit(1); }
}
static void *Load(NSString *path) {
    void *handle = dlopen(path.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    if (!handle) { std::fprintf(stderr, "dlopen: %s\n", dlerror()); std::exit(2); }
    return handle;
}
template <typename T> static T Symbol(void *library, const char *name) {
    T value = reinterpret_cast<T>(dlsym(library, name));
    Require(value != nullptr, name);
    return value;
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 2) return 2;
        const std::string mode = argv[1];
        const bool notices = mode == "notices";
        Require(notices || mode == "baseline" || mode == "original", "known test mode");
        [NSUserDefaults.standardUserDefaults setVolatileDomain:@{@"AppleLanguages": @[@"en"]}
                                                      forName:NSArgumentDomain];
        NSString *resources = [NSBundle.mainBundle.bundlePath stringByAppendingPathComponent:@"Contents/Resources"];
        Load([resources stringByAppendingPathComponent:@"WeChatTool/WeChatTool.dylib"]);
        void *library = Load([resources stringByAppendingPathComponent:@"wechat.dylib"]);
        using Handler = bool (*)(const void *, const void *);
        const auto predicate = Symbol<bool (*)(const void *)>(library, "is_revoke");
        const auto handler = Symbol<Handler>(library, "notice_handler");
        const auto originalCalls = Symbol<unsigned (*)()>(library, "original_calls");
        const auto count = Symbol<unsigned (*)()>(library, "emitted_count");
        const auto session = Symbol<const char *(*)()>(library, "emitted_session");
        const auto text = Symbol<const char *(*)()>(library, "emitted_text");
        const auto setContext = Symbol<void (*)(bool)>(library, "set_context");
#if defined(__arm64__)
        constexpr std::array<uint8_t, 20> original = {
            0x08, 0x0c, 0x40, 0xb9, 0x49, 0xe2, 0x84, 0x52,
            0x1f, 0x01, 0x09, 0x6b, 0xe0, 0x17, 0x9f, 0x1a,
            0xc0, 0x03, 0x5f, 0xd6};
        constexpr std::array<uint8_t, 8> jump = {0x50, 0, 0, 0x58, 0, 0x02, 0x1f, 0xd6};
        constexpr std::array<uint8_t, 4> replacement = {0, 0, 0x80, 0x52};
        constexpr std::array<uint8_t, 4> nop = {0x1f, 0x20, 0x03, 0xd5};
        constexpr size_t replacementOffset = 12;
#else
        constexpr std::array<uint8_t, 16> original = {
            0x55, 0x48, 0x89, 0xe5, 0x81, 0x7f, 0x0c, 0x12,
            0x27, 0, 0, 0x0f, 0x94, 0xc0, 0x5d, 0xc3};
        constexpr std::array<uint8_t, 6> jump = {0xff, 0x25, 0, 0, 0, 0};
        constexpr std::array<uint8_t, 3> replacement = {0x31, 0xc0, 0x90};
        constexpr std::array<uint8_t, 1> nop = {0x90};
        constexpr size_t replacementOffset = 11;
#endif
        auto expected = original;
        if (mode != "original")
            std::memcpy(expected.data() + replacementOffset, replacement.data(), replacement.size());
        Require(std::memcmp(reinterpret_cast<const void *>(predicate), expected.data(), expected.size()) == 0,
                "predicate uses exact small preservation patch independently of notice detour");
        const auto *handlerCode = reinterpret_cast<const uint8_t *>(handler);
        if (notices) {
            Require(std::memcmp(handlerCode, jump.data(), jump.size()) == 0, "separate handler detour installed");
            uintptr_t callback = 0;
            std::memcpy(&callback, handlerCode + jump.size(), sizeof(callback));
            Require(callback != 0, "handler callback exists");
        } else {
            for (size_t i = 0; i < 32; i += nop.size())
                Require(std::memcmp(handlerCode + i, nop.data(), nop.size()) == 0, "fallback leaves native handler unchanged");
        }
        alignas(16) std::array<uint8_t, 632> record{};
        static_assert(sizeof(std::string) == 24, "Fixture needs reviewed libc++ ABI");
        std::string recordSession = "fixture_session";
        std::string xml;
        uint32_t type = 10002;
        auto prepare = [&](const std::string &identifier, const std::string &notice) {
            xml = "<sysmsg type=\"revokemsg\"><revokemsg><session>fixture_session</session><newmsgid>" +
                identifier + "</newmsgid><replacemsg><![CDATA[" + notice + "]]></replacemsg></revokemsg></sysmsg>";
            std::memcpy(record.data() + 12, &type, sizeof(type));
            std::memcpy(record.data() + 24, &recordSession, sizeof(recordSession));
            std::memcpy(record.data() + 304, &xml, sizeof(xml));
        };
        prepare("123", "Alice recalled a message");
        Require(predicate(record.data()) == (mode == "original"), "recall preservation result");
        Require(handler(nullptr, record.data()), "handler consumes recall");
        if (notices) {
            const bool chinese = [NSLocale.preferredLanguages.firstObject hasPrefix:@"zh"];
            const std::string suffix = chinese ? "（已阻止本机撤回）" : " (recall blocked on this Mac)";
            Require(originalCalls() == 0, "detour never invokes destructive original handler");
            Require(count() == 1, "first recall emitted synchronously");
            Require(std::string(session()) == recordSession, "emitter received parsed conversation");
            Require(std::string(text()) == "Alice recalled a message" + suffix, "emitter received parsed notice text");
            Require(handler(nullptr, record.data()), "duplicate consumed");
            Require(count() == 1, "duplicate notice suppressed");
            prepare("124", "Bob recalled a message");
            setContext(false);
            Require(handler(nullptr, record.data()) && count() == 1, "missing native context skips emission");
            setContext(true);
            Require(handler(nullptr, record.data()) && count() == 2, "context refusal does not reserve duplicate key");
            xml = "<sysmsg>broken";
            std::memcpy(record.data() + 304, &xml, sizeof(xml));
            Require(handler(nullptr, record.data()) && count() == 2, "malformed XML consumed without notice");
            Require(handler(nullptr, nullptr) && handler(nullptr, reinterpret_cast<const void *>(1)),
                    "unreadable wrapper safely consumed");
            prepare("125", "小明撤回了一条消息");
            Require(handler(nullptr, record.data()) && count() == 3, "Chinese metadata emits");
            Require(std::string(text()) == "小明撤回了一条消息" + suffix, "Chinese text preserved exactly");
            recordSession = "different_session";
            prepare("126", "Alice recalled a message");
            Require(handler(nullptr, record.data()) && count() == 3, "mismatched conversation rejected");
            Require(originalCalls() == 0, "refusal paths never call original handler");
        } else {
            Require(originalCalls() == 1 && count() == 0, "fallback retains native handler without emitter");
        }
        for (const uint32_t normalType : {0u, 1u, 10000u}) {
            std::memcpy(record.data() + 12, &normalType, sizeof(normalType));
            Require(!predicate(record.data()), "ordinary message predicate unchanged");
        }
        Require(NSClassFromString(@"NSApplication") == nil, "fixture never links AppKit or creates windows");
        return 0;
    }
}
#endif
