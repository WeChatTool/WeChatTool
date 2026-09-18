// SPDX-License-Identifier: MIT
// Harmless local predicates for native patch integration, never WeChat code.
.text
.p2align 2
.globl _fixture_interface
_fixture_interface:
#if defined(__arm64__)
    cbz x0, Lnull
    adrp x8, _fixture_enabled@PAGE
    ldrb w8, [x8, _fixture_enabled@PAGEOFF]
    cbz w8, Lnull
    mov w0, #7
    ret
Lnull:
    mov w0, #0
    ret
#elif defined(__x86_64__)
    testq %rdi, %rdi
    je Lnull
    cmpb $0, _fixture_enabled(%rip)
    je Lnull
    movl $7, %eax
    ret
Lnull:
    xorl %eax, %eax
    ret
#endif
.p2align 2
.globl _fixture_actions
_fixture_actions:
#if defined(__arm64__)
    adrp x8, _fixture_enabled@PAGE
    ldrb w8, [x8, _fixture_enabled@PAGEOFF]
    mov w0, #0
    ret
#elif defined(__x86_64__)
    cmpb $0, _fixture_enabled(%rip)
    movl $0, %eax
    ret
#endif
.p2align 2
.globl _fixture_end
_fixture_end:
#if defined(__arm64__)
    ret
#elif defined(__x86_64__)
    ret
#endif
.data
_fixture_enabled:
    .byte 0
