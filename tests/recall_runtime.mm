// SPDX-License-Identifier: MIT
// All messages, contexts and memory regions are disposable synthetic fixtures.
#import <Foundation/Foundation.h>
#import "RecallRuntime.h"
#include <array>
#include <atomic>
#include <condition_variable>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <sys/mman.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {

#if defined(__arm64__)
constexpr size_t kPointerWord = 0, kCapacityWord = 16, kShortSizeByte = 23;
constexpr uint8_t kInvalidShortSize = 23;
constexpr uint64_t kLongFlag = UINT64_C(0x8000000000000000);
constexpr size_t kModeOffset = 0x288, kCounterOffset = 0x3c8;
#elif defined(__x86_64__)
constexpr size_t kPointerWord = 16, kCapacityWord = 0, kShortSizeByte = 0;
constexpr uint8_t kInvalidShortSize = 23 << 1;
constexpr uint64_t kLongFlag = 1;
constexpr size_t kModeOffset = 0x188, kCounterOffset = 0x2c8;
#else
#error Unsupported test architecture
#endif

struct FakeMessage {
    std::array<uint8_t, 632> bytes{};
    std::string session, xml;
    FakeMessage(std::string target, std::string content) : session(std::move(target)), xml(std::move(content)) {
        type(10002); string(24, session); string(304, xml);
    }
    void type(uint32_t value) { std::memcpy(bytes.data() + 12, &value, sizeof(value)); }
    void word(size_t offset, uint64_t value) { std::memcpy(bytes.data() + offset, &value, sizeof(value)); }
    void string(size_t offset, const std::string &value) {
        // Exercise the actual target CPU's libc++ ABI, including real heap strings.
        static_assert(sizeof(value) == 24, "unexpected fixture string ABI");
        std::memcpy(bytes.data() + offset, &value, sizeof(value));
    }
};

struct FakeTask {
    std::array<uint8_t, kCounterOffset + 4> object{};
    std::array<int64_t, 2> control{};
    std::array<uintptr_t, 2> holder{};
    const void *slot = nullptr;
    FakeTask() {
        holder = {reinterpret_cast<uintptr_t>(object.data()), reinterpret_cast<uintptr_t>(control.data())};
        slot = holder.data();
        // Mode is checked for readability, not against an invented enum domain.
        uint32_t mode = 99;
        std::memcpy(object.data() + kModeOffset, &mode, sizeof(mode));
    }
};

std::string XML(const std::string &session, uint64_t identifier, const std::string &actor = "Alice") {
    return "<sysmsg type=\"revokemsg\"><revokemsg><session>" + session +
           "</session><newmsgid>" + std::to_string(identifier) +
           "</newmsgid><replacemsg><![CDATA[\"" + actor +
           "\" recalled a message]]></replacemsg></revokemsg></sysmsg>";
}

FakeTask gDefaultTask, gOtherTask;
thread_local const void *gSlotAddress = &gDefaultTask.slot;
std::atomic<unsigned> gGetterMode{0}, gEmitterMode{0}, gFalseResults{0}, gWrongEmitter{0};
std::atomic<bool> gSnapshotFailure{false};
std::mutex gMutex, gGateMutex;
std::condition_variable gGate;
std::vector<std::pair<std::string, std::string>> gEmitted;
bool gEntered = false, gRelease = false;
const void *gNestedMessage = nullptr;
FakeMessage *gMutableMessage = nullptr;
std::vector<std::unique_ptr<FakeTask>> gCascadeTasks;
std::vector<std::unique_ptr<FakeMessage>> gCascadeMessages;
size_t gCascadeDepth = 0;
unsigned gChecks = 0;

void Require(bool valid, const char *message) { if (!valid) throw std::runtime_error(message); }
void Hook(const void *message) { if (!WCTHandleRecallMessage(nullptr, message)) ++gFalseResults; }
const void *TaskSlot() {
    if (gGetterMode == 1) throw std::runtime_error("synthetic getter failure");
    if (gGetterMode == 2) @throw [NSException exceptionWithName:@"SyntheticGetterFailure" reason:nil userInfo:nil];
    return gSlotAddress;
}
struct TaskScope {
    const void *previous = gSlotAddress;
    explicit TaskScope(const void *slot) { gSlotAddress = slot; }
    ~TaskScope() { gSlotAddress = previous; }
};
void Emitter(uintptr_t unused, const std::string *session, const std::string *text) {
    Require(!unused && session && text, "unexpected emitter ABI");
    {
        std::lock_guard<std::mutex> lock(gMutex);
        gEmitted.emplace_back(*session, *text);
    }
    switch (gEmitterMode.load()) {
        case 1: throw std::runtime_error("synthetic emitter failure");
        case 2: @throw [NSException exceptionWithName:@"SyntheticEmitterFailure" reason:nil userInfo:nil];
        case 3: Hook(gNestedMessage); break;
        case 4: { TaskScope task(&gOtherTask.slot); Hook(gNestedMessage); break; }
        case 5: {
            const auto before = std::make_pair(*session, *text);
            gMutableMessage->bytes.fill(0);
            gMutableMessage->session.assign(10000, 'x');
            gMutableMessage->xml.assign(10000, 'y');
            if (*session != before.first || *text != before.second) gSnapshotFailure = true;
            break;
        }
        case 6: {
            std::unique_lock<std::mutex> lock(gGateMutex);
            if (!gEntered) { gEntered = true; gGate.notify_all(); gGate.wait(lock, [] { return gRelease; }); }
            break;
        }
        case 7: {
            const size_t depth = gCascadeDepth;
            if (depth + 1 < gCascadeMessages.size()) {
                gCascadeDepth = depth + 1;
                TaskScope task(&gCascadeTasks[gCascadeDepth]->slot);
                Hook(gCascadeMessages[gCascadeDepth]->bytes.data());
                gCascadeDepth = depth;
            }
            break;
        }
        default: break;
    }
}
void OtherEmitter(uintptr_t, const std::string *, const std::string *) { ++gWrongEmitter; }
size_t Count() { std::lock_guard<std::mutex> lock(gMutex); return gEmitted.size(); }
std::pair<std::string, std::string> Last() { std::lock_guard<std::mutex> lock(gMutex); return gEmitted.back(); }
void Pass(const char *name) {
    Require(!gFalseResults.load(), "handler did not consume a recall");
    ++gChecks; std::cout << "PASS " << name << '\n';
}

void Tests() {
    Hook(nullptr); Hook(reinterpret_cast<void *>(1));
    WCTConfigureRecallNotices(nullptr, TaskSlot);
    WCTConfigureRecallNotices(Emitter, nullptr);
    FakeMessage first("chat-A", XML("chat-A", 1));
    Hook(first.bytes.data());
    Require(Count() == 0, "invalid configuration activated runtime");
    WCTConfigureRecallNotices(Emitter, TaskSlot);
    WCTConfigureRecallNotices(OtherEmitter, TaskSlot, true);
    Pass("unconfigured and invalid configurations consume without emission");

    Hook(nullptr); Hook(reinterpret_cast<void *>(1));
    Hook(reinterpret_cast<void *>(std::numeric_limits<uintptr_t>::max() - 8));
    const size_t pageSize = static_cast<size_t>(getpagesize());
    void *pages = mmap(nullptr, pageSize * 2, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    Require(pages != MAP_FAILED, "mmap failed");
    uint8_t *unreadable = static_cast<uint8_t *>(pages) + pageSize;
    Require(mprotect(unreadable, pageSize, PROT_NONE) == 0, "mprotect failed");
    Hook(unreadable);
    uint8_t *truncated = unreadable - 16;
    const uint32_t revoke = 10002;
    std::memcpy(truncated + 12, &revoke, sizeof(revoke)); Hook(truncated);
    FakeMessage inaccessible("chat-A", XML("chat-A", 2));
    inaccessible.word(304 + kPointerWord, reinterpret_cast<uintptr_t>(unreadable)); Hook(inaccessible.bytes.data());
    Require(munmap(pages, pageSize * 2) == 0, "munmap failed");
    Require(Count() == 0, "invalid message memory emitted");
    Pass("guarded reads reject invalid, overflowing and truncated message memory");

    FakeMessage malformed("chat-A", XML("chat-A", 3));
    for (uint32_t type : {1u, 10000u}) { malformed.type(type); Hook(malformed.bytes.data()); }
    malformed.type(10002);
    malformed.word(312, 65537); Hook(malformed.bytes.data());
    malformed.string(304, malformed.xml);
    malformed.word(304 + kCapacityWord, kLongFlag | malformed.xml.size()); Hook(malformed.bytes.data());
    malformed.string(304, malformed.xml);
    malformed.word(304 + kPointerWord, UINTPTR_MAX - 3); Hook(malformed.bytes.data());
    malformed.string(304, malformed.xml);
    malformed.bytes[304 + kShortSizeByte] = kInvalidShortSize; Hook(malformed.bytes.data());
    for (auto pair : {std::make_pair(std::string("chat-A"), std::string("not XML")),
                      std::make_pair(std::string("chat-A"), std::string("\xff\xfe", 2)),
                      std::make_pair(std::string(""), XML("chat-A", 3)),
                      std::make_pair(std::string("chat-B"), XML("chat-A", 3)),
                      std::make_pair(std::string("\xff", 1), XML("chat-A", 3))}) {
        FakeMessage invalid(pair.first, pair.second); Hook(invalid.bytes.data());
    }
    Require(Count() == 0, "invalid strings, XML or session emitted");
    Pass("type, libc++ string bounds, UTF-8, XML and exact session checks");

    for (const void *slot : {static_cast<const void *>(nullptr), reinterpret_cast<const void *>(1)}) {
        TaskScope task(slot); Hook(first.bytes.data());
    }
    FakeTask badTask;
    { TaskScope task(&badTask.slot);
      badTask.slot = nullptr; Hook(first.bytes.data());
      badTask.slot = reinterpret_cast<void *>(1); Hook(first.bytes.data());
      badTask.slot = badTask.holder.data();
      badTask.control[1] = -1; Hook(first.bytes.data()); badTask.control[1] = 0;
      const auto saved = badTask.holder;
      for (size_t word : {size_t(0), size_t(1)}) {
          badTask.holder[word] = 0; Hook(first.bytes.data());
          badTask.holder[word] = 1; Hook(first.bytes.data());
          badTask.holder = saved;
      }
    }
    for (unsigned mode : {1u, 2u}) { gGetterMode = mode; Hook(first.bytes.data()); }
    gGetterMode = 0;
    Require(Count() == 0, "invalid or expired native task emitted");
    Hook(first.bytes.data());
    Require(Count() == 1 && !gWrongEmitter.load(), "valid retry/configuration failed");
    Require(Last().first == "chat-A" && Last().second == "\"Alice\" recalled a message (recall blocked on this Mac)",
            "native notice payload differs from parsed output");
    Hook(first.bytes.data()); Require(Count() == 1, "completed duplicate emitted");
    Pass("task slot, weak holder, context, expired owner and getter exception guards; valid retries");

    const std::string longSession = "synthetic-session-identifier-longer-than-22";
    FakeMessage longRecord(longSession, XML(longSession, 1)); Hook(longRecord.bytes.data());
    Require(Count() == 2 && Last().first == longSession, "session-scoped key or long string failed");
    FakeMessage captured("captured-chat", XML("captured-chat", 4, "Snapshot Person"));
    gMutableMessage = &captured; gEmitterMode = 5; Hook(captured.bytes.data()); gEmitterMode = 0;
    Require(Count() == 3 && !gSnapshotFailure && Last().second.find("Snapshot Person") != std::string::npos,
            "owned snapshot failed");
    Pass("synchronous native emission owns strings across mutation of the original record");

    FakeMessage outer("recursive-chat", XML("recursive-chat", 6));
    FakeMessage inner("recursive-chat", XML("recursive-chat", 7));
    gNestedMessage = inner.bytes.data(); gEmitterMode = 3; Hook(outer.bytes.data()); gEmitterMode = 0;
    Require(Count() == 4, "same-context recursion emitted");
    Hook(inner.bytes.data()); Require(Count() == 5, "reentrant refusal consumed a key");
    FakeMessage switching("switching-chat", XML("switching-chat", 8));
    FakeMessage different("switching-chat", XML("switching-chat", 9));
    gNestedMessage = different.bytes.data(); gEmitterMode = 4; Hook(switching.bytes.data()); gEmitterMode = 0;
    Require(Count() == 7, "another coroutine on the same OS thread was suppressed");
    Pass("active-context recursion guards permit another task on the same OS thread");

    for (unsigned mode : {1u, 2u}) {
        FakeMessage throwing("exception-chat", XML("exception-chat", 10 + mode));
        const size_t before = Count();
        gEmitterMode = mode; Hook(throwing.bytes.data()); gEmitterMode = 0;
        Hook(throwing.bytes.data()); Hook(throwing.bytes.data());
        Require(Count() == before + 2, "emitter failure was not retryable or success did not deduplicate");
    }
    Pass("C++ and Objective-C emitter failures release context and dedup reservations for retry");

    FakeMessage pending("pending-chat", XML("pending-chat", 12));
    const size_t beforeParallel = Count();
    gEmitterMode = 6;
    std::thread worker([&] { FakeTask ownTask; TaskScope task(&ownTask.slot); Hook(pending.bytes.data()); });
    { std::unique_lock<std::mutex> lock(gGateMutex); gGate.wait(lock, [] { return gEntered; }); }
    Hook(pending.bytes.data());
    FakeMessage unrelated("pending-chat", XML("pending-chat", 13)); Hook(unrelated.bytes.data());
    { std::lock_guard<std::mutex> lock(gGateMutex); gRelease = true; }
    gGate.notify_all(); worker.join(); gEmitterMode = 0;
    Require(Count() == beforeParallel + 2, "pending duplicate or global lock blocked another context");
    Pass("pending deduplication and other-context concurrency without a mutex across native emission");

    for (uint64_t i = 0; i < 513; ++i) {
        gCascadeTasks.push_back(std::make_unique<FakeTask>());
        gCascadeMessages.push_back(std::make_unique<FakeMessage>("bounded-chat", XML("bounded-chat", 1000 + i)));
    }
    const size_t beforeBound = Count();
    gEmitterMode = 7;
    { TaskScope task(&gCascadeTasks[0]->slot); Hook(gCascadeMessages[0]->bytes.data()); }
    gEmitterMode = 0;
    Require(Count() == beforeBound + 512, "pending context/dedup capacity is not bounded at512");
    Hook(gCascadeMessages[0]->bytes.data()); Require(Count() == beforeBound + 512, "pending entry was evicted");
    Hook(gCascadeMessages[512]->bytes.data()); Require(Count() == beforeBound + 513, "capacity refusal consumed key");
    Hook(gCascadeMessages[0]->bytes.data()); Require(Count() == beforeBound + 514, "old completed key was not evicted");
    Pass("512-entry bound never evicts pending contexts and permits retries after capacity refusal");
}

} // namespace

int main() {
    @autoreleasepool {
        try { Tests(); }
        catch (const std::exception &error) { std::cerr << "FAIL " << error.what() << '\n'; return 1; }
        std::cout << gChecks << " synchronous recall-runtime groups passed\n";
    }
    return 0;
}
