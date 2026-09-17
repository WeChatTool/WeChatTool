// SPDX-License-Identifier: MIT
#pragma once

#include <cstdint>
#include <string>

// Both function addresses must come from a reviewed, loaded-image profile.
using WCTRecallEmitter = void (*)(uintptr_t unused,
                                 const std::string *session,
                                 const std::string *text);
using WCTTaskSlotGetter = const void *(*)();

// First nonnull configuration wins and remains alive for the process lifetime.
// The getter returns the ADDRESS of WeChat's current-task TLS slot. Emission is
// synchronous within the reviewed native handler and may yield its coroutine.
void WCTConfigureRecallNotices(WCTRecallEmitter emitter,
                              WCTTaskSlotGetter taskSlotGetter,
                              bool chinese = false) noexcept;

// Consume the original recall on every path, including malformed input and
// emitter failure. The independent preservation predicate remains separate.
extern "C" bool WCTHandleRecallMessage(const void *service, const void *message) noexcept;
