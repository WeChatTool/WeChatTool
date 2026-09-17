# Compatibility and adaptation

WeChat exposes no stable anti-recall plugin interface. This project uses a narrow native-code modification, with separate checks for structural compatibility and real message behavior.

## What is verified

The analyzer reads the app's Mach-O files and checks:

1. The main bundle is `com.tencent.xinWeChat` and its executable is `WeChat`.
2. Each launcher architecture has exactly one supported predicate across the candidate core images.
3. The complete predicate matches a known instruction sequence in executable `__TEXT,__text`, at an `LC_FUNCTION_STARTS` boundary.
4. The image has a UUID. The output records that UUID, architecture, address, exact bytes, bundle version/build, and SHA-256 of the source image.

The default candidate paths are `Contents/Resources/wechat.dylib` and `Contents/Frameworks/wechat.dylib`. The Resources image contains the implementation in the inspected 4.1.15 installation; the similarly named Frameworks image is a stub. `--image` accepts an explicit bundle-relative path for a changed layout. Paths must remain within the bundle. Hooks in the main executable are rejected because that image is already loaded when the plugin initializes.

Preparation re-analyzes the staged copy to catch source changes during copying. It does not publish the output unless the copy can be signed and verified. Runtime checks restrict the plan to the current app version/build, the intended loaded image and UUID, and the supported instruction recipe with its expected bytes. A mismatch leaves the affected image unmodified.

These checks prevent reusing a stale offset or selecting an ambiguous signature. They cannot prove that WeChat's surrounding recall workflow has retained its meaning. The reported status `structurally-compatible` explicitly does not mean live behavior has passed testing.

## Inspected installation

WeChat **4.1.15 / 270099** was inspected read-only. Both slices contain one matching predicate at a recorded function start:

| Architecture | Predicate virtual address | Modified instruction | Image UUID |
| --- | --- | --- | --- |
| arm64 | `0x48db554` | `0x48db560` | `ED4DCBD2-4896-3A6D-8A70-7D8D88F74B0D` |
| x86_64 | `0x5037860` | `0x503786b` | `97E21436-ABDA-3B79-BEC0-EF2653C6B423` |

Core: `Contents/Resources/wechat.dylib`.

SHA-256: `65117e24ca1a8b66aff122db2e86dbd544a8dc98064526440d1ffba690fd9a69`.

These addresses document the inspected file; the analyzer searches the instructions and does not use these addresses as fallbacks. Live recall prevention has been reported working on this build. This is not a verification of every architecture, message type, or synchronization path.

## Instruction recipes

Both supported predicates return whether a 32-bit field at message offset `0x0c` equals `10002` (`0x2712`). The preservation patch changes only the boolean-result instruction:

| Architecture | Exact original predicate bytes | Offset within predicate | Replacement bytes |
| --- | --- | --- | --- |
| arm64 | `080c40b949e284521f01096be0179f1ac0035fd6` | `12` | `00008052` |
| x86_64 | `554889e5817f0c122700000f94c05dc3` | `11` | `31c090` |

On arm64, `CSET W0, EQ` becomes `MOV W0, #0`. On Intel, `SETE AL` becomes `XOR EAX, EAX; NOP`. This patch keeps original loads, comparisons, function boundaries, and return instructions intact. Optional notices intercept a separate function, described below.

Read-only inspection of the four direct arm64 callers in build 270099 shows that this predicate selects system/revoke message extension construction and access. This supports the intended interception point but does not establish coverage of every deletion or synchronization path.

The ARM preservation-only instruction write is aligned and its instruction cache is invalidated. The Intel replacement and the optional notice detours span multiple instructions and are not atomic. On both architectures, changing page protection temporarily removes execution permission, so even an atomic ARM store is unsuitable for patching a core that may already be executing.

The plugin records the images already loaded when it initializes and refuses to patch any of those images (`late-image-refused`). It patches a matching newly loaded image only from the dyld image-add callback, before that image's initializers run. This is not a hot-patching mechanism. An app version that loads its core before the plugin initializes cannot activate through this path, even if static analysis succeeds. Disabling or enabling the plugin takes effect on the next process launch.

This also depends on the core being visible to dyld's image callbacks. A custom loader that maps the implementation without those callbacks is not covered. Read-only inspection of build 270099 places the primary-load call under the launcher's `_LdMain` entry, but its wrapper has multiple loading paths; other loading paths still require runtime verification.

## Optional permanent recall notices

Message preservation and notice support have separate compatibility requirements.
`wechattool/notice_profiles.json` lists reviewed adapters for 4.1.15 / 270099 on
both architectures. The analyzer selects an adapter only when version, build,
architecture, UUID, predicate address, and the complete source-image SHA-256
match. The same profiles are compiled into the plugin. A generated app plan
cannot introduce object offsets, executable bytes, or callback addresses.
Malformed or ambiguous profiles are rejected.

The preservation patch above is always installed first. A matching notice
adapter then verifies SHA-256 hashes of three complete native functions: the
inner recall handler, the local system-message helper, and the current-task TLS
slot getter. Only the handler entry is replaced with a fixed absolute jump to
the plugin: 16 bytes on arm64, 14 on Intel. Unknown layouts or mismatched helper
code leave the existing preservation patch active without notices.

| Native function | arm64 address | x86_64 address |
| --- | --- | --- |
| Inner recall handler | `0x30c5298` | `0x35594b0` |
| Local system-message helper | `0x42e6c1c` | `0x49a3f90` |
| Current-task TLS slot getter | `0x64ff910` | `0x6f51a60` |

The raw XML dispatcher selects the recall handler independently of the patched
predicate. Its outer handler constructs a complete 632-byte MessageWrap, calls
the inner handler, destroys the wrapper, and reports the event handled. The
plugin intercepts this inner boundary and returns true without running the
original deletion/replacement path. It does not build a trampoline or execute
displaced instructions.

The reviewed record has a type at `0x0c`, a session string at `0x18`, and recall
XML at `0x130`. The arm64 slice uses libc++'s alternate string layout; Intel uses
its default layout. Reads use bounded `mach_vm_read_overwrite` snapshots and
validate short/long string representations before parsing. Ordinary message
contents are not captured. XML must contain a valid revoke event, positive
message ID, matching session, and replacement text. External entities, DTDs,
ambiguous fields, oversized inputs, and invalid UTF-8 are rejected.

The native helper creates a local type-10000 system message, with a fresh local
identity and server ID zero, using the session and notice text. WeChat stores and
displays this row through its own local message and session services. It contains
WeChat's supplied recall text plus a local recall-blocked suffix. The plugin does
not directly open databases or send an outgoing message. WeChat retains control
of local history storage and deletion.

The helper must run synchronously inside the intercepted WeChat task; a Cocoa or
GCD queue does not provide the required private coroutine context. Before calling
it, the plugin checks the nullable TLS slot, its weak-pointer holder, the task
and control block, reference-count liveness, and readable context fields. Context
layout differs by architecture. Native services may yield, so the plugin holds
no mutex across the helper and tracks reentrancy per task, not per OS thread.
Session/text snapshots are owned across the call. A bounded cache deduplicates
session/message-ID pairs within the current process; it is not a persistent
cross-launch deduplication database.

The original native recall path replaces the original message with its notice.
Merely skipping deletion or pretending that the original is absent is inadequate:
its missing-message path drops group recalls, and the generated native notice
still inherits the original server ID. Using the separate local-message helper
preserves the original identity and avoids that replacement path.

Invalid metadata or unavailable context suppresses the notice while the event is
still consumed and the preservation patch remains active. Compatibility and
synthetic parser, guarded-memory, native-hook, and packaging tests do not prove
live behavior in WeChat. Verify preservation, the displayed actor, group chats,
and notice persistence after reopening the conversation and restarting WeChat.

## Extending compatibility

A future build with an unchanged, unique predicate may pass structural analysis without a new fixed-offset profile. Test its real behavior before relying on it. Missing or ambiguous matches require investigation; there is no nearest-version address fallback.

For a new recipe:

1. Inspect a clean app read-only and establish the candidate function's complete instruction semantics and callers.
2. Check every target architecture separately. A byte pattern must identify a complete supported predicate at a recorded function start, not a coincidental constant or a fragment inside another function.
3. Add the declarative profile and implement the corresponding native recipe validation. Changing `profiles.json` alone must not enable arbitrary address writes or arbitrary machine code.
4. Add tests for the new match, incorrect function boundaries, missing/duplicate matches, malformed Mach-O input, stale UUID/version plans, and rejected runtime recipes as appropriate.
5. Prepare a fresh copy, check startup diagnostics, and verify recall behavior with consent-based test messages, including restart persistence and relevant message types.

The existing implementation does not include the older Objective-C `MessageService` swizzling approach. Supporting 3.x requires a separately reviewed adapter, not relaxing the current signature checks.

## App copies, signing, and updates

Preparation never replaces the source installation or an existing output path. The copied launcher gains an `LC_LOAD_DYLIB` dependency on the local plugin; a generated plan lives under `Contents/Resources/WeChatTool/`. The source core image is not patched on disk. The native plugin changes only its own process memory after validation.

The copy retains the original bundle ID and may access the same chat storage as the original app. This does not guarantee that the re-signed app can reopen existing databases or resolve security-scoped bookmarks. Never run both copies concurrently. Removing the copy does not require deleting account data.

Keep an independent chat backup before testing a modified client. The plugin does not manage chat backups or restore missing history.

Ad-hoc signing changes the app's identity. Preparation retains original entitlements and adds `com.apple.security.cs.disable-library-validation` and `com.apple.security.cs.allow-unsigned-executable-memory`. It does not request a debugger entitlement or change SIP. Preserving entitlement keys cannot preserve Tencent's signing identity, so operating-system permission checks, launch/login behavior, or other app features may still differ.

Automatic updating is not disabled. An update can replace the launcher, plugin resources, or core image, so a prepared copy is not an update-management solution. After updates, use the clean official app as the source for a new analysis and preparation. Do not copy an old plan into a new build.

## Diagnostics and disabling

The plugin reports compatibility and patch status through macOS unified logging, without message content or account identifiers. A successful status reports that the instruction change was applied; it does not certify actual recall preservation.

Start this command before launching the copied app:

```sh
log stream --style compact --level info --predicate 'subsystem == "local.wechattool"'
```

The log category is `runtime`. `initialized-active` or a later `active` entry indicates the hook was applied. `initialized-no-image-matched` means the plugin is waiting for a matching image. `initialized-refused` means a matching image was found but a validation or timing guard refused the patch. `late-image-refused` means the core was already loaded when the plugin initialized and was left unchanged.

`recall-notices-active` means the optional observer was installed. To troubleshoot
display separately while retaining preservation, launch the copied executable
with `WECHATTOOL_NOTICES=0`. This switch also takes effect only on process launch.

The preferred startup disable switch is:

```sh
WECHATTOOL_DISABLE=1 /path/to/copied.app/Contents/MacOS/WeChat
```

It must be set for the copied executable's process. Setting it in a shell and using `open` may not propagate it through LaunchServices, so the command above launches the executable directly. Quit any running WeChat first.

The alternative resource marker is an empty file at `Contents/Resources/WeChatTool/disabled` inside the copied app, and is also startup-only. Adding it after preparation modifies the signed resource seal, so use the environment switch for normal troubleshooting. Rebuild and prepare a fresh copy to restore a clean, signed output after editing its resources.
