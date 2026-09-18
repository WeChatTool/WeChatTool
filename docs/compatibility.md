# Compatibility and adaptation

WeChat exposes no stable plugin interface for these changes. This project uses narrow native-code modifications, with separate checks for structural compatibility and live behavior. Each prepared app selects recall protection, experimental accessibility support, or both. The default remains recall protection only.

## What is verified

The analyzer reads the app's Mach-O files and checks:

1. The source is the official `com.tencent.xinWeChat` bundle and its executable is `WeChat`. Prepared installations are not accepted as source apps.
2. Each launcher architecture has the required supported targets for every selected feature across the candidate core images.
3. For recall protection, the complete predicate matches a known instruction sequence in executable `__TEXT,__text`, at an `LC_FUNCTION_STARTS` boundary. Accessibility support uses exact-build profiles and validates its required instruction sequences separately.
4. The image has a UUID. The output records that UUID, architecture, address, exact bytes, bundle version/build, and SHA-256 of the source image.

The default candidate paths are `Contents/Resources/wechat.dylib` and `Contents/Frameworks/wechat.dylib`. The Resources image contains the implementation in the inspected 4.1.15 installation; the similarly named Frameworks image is a stub. `--image` accepts an explicit bundle-relative path for a changed layout. Paths must remain within the bundle. Hooks in the main executable are rejected because that image is already loaded when the plugin initializes.

Preparation re-analyzes the staged copy to catch source changes during copying. It does not publish the output unless the copy can be signed and verified. Runtime checks restrict the plan to the current app version/build, the intended loaded image and UUID, and the supported instruction recipe with its expected bytes. A mismatch leaves the affected image unmodified.

These checks prevent reusing a stale offset or selecting an ambiguous signature. They cannot prove that WeChat's surrounding workflows have retained their meaning. The reported status `structurally-compatible` explicitly does not mean live behavior has passed testing. Analysis and preparation reports include the selected `features`, which the installer checks against the requested selection.

## Feature selection

The installer has independent **Recall protection** and **Accessibility support (experimental)** checkboxes. Recall protection is on by default; accessibility support is off. At least one must be selected. Changing a selection clears the prior compatibility result and completed-copy controls, then checks the source again. Each newly prepared copy records only its selected features.

The command-line equivalent is `--features recall`, `--features accessibility`, or `--features recall accessibility` on both `analyze` and `prepare`. Omitting the option selects recall protection. A requested feature that is unsupported for any architecture makes the full request unsupported; there is no silent fallback to another feature.

## Experimental accessibility support

The accessibility profiles cover the inspected **4.1.15 / 270099** core on arm64 and x86_64. They keep widget accessibility interfaces available and allow the custom accessibility actions that the core otherwise limits after mouse input. The runtime validates the original build, image identity, and expected bytes before applying either change. This is an optional local UI automation feature, not a login or session-restoration feature.

Static tracing found a restriction-configured accessibility gate and a separate action predicate in the original WeChat core. It did not establish that either causes the account-security logout warning. This feature does not disable server-side restrictions, prevent “For account security, log in again,” or replace the automation tool's macOS accessibility permission. Live automation behavior has not yet been verified.

The changes apply only in the prepared app's process memory. The official source app is unchanged. To remove this feature, create a new copy with recall protection alone; the startup disable switch below temporarily disables all selected patches for one launch.

The reviewed [accessibility profiles](../wechattool/accessibility_profiles.json) contain the complete original functions and their hashes. Addresses below are unslid virtual addresses for this build, not file offsets:

| Architecture | Function | Start | Patch offset | Change |
| --- | --- | --- | --- | --- |
| arm64 | Interface lookup | `0x6ad7210` | `48` | Replace the enabled-flag `CBZ` with a `NOP`. |
| x86_64 | Interface lookup | `0x744e980` | `39` | Replace the enabled-flag `JE` with six `NOP` instructions. |
| arm64 | Action permission | `0x1fc894` | `0` | Return true with `MOV W0, #1; RET`. |
| x86_64 | Action permission | `0x236210` | `0` | Return true with `MOV EAX, 1; RET; NOP`. |

The interface patches retain the earlier null-object checks, lookup logic, object lifetime handling, and return paths. The action replacements return before any original stack or callee-saved-register changes. They bypass the local click latch and time check for this predicate. Neither patch changes the restriction configuration, persisted key, state reporting, or logout code.

Both function hashes and the complete on-disk image hash are checked before either accessibility patch is written. The allowlist is compiled into the plugin, so editing an app plan cannot introduce a new patch. Native tests use harmless fixture libraries to verify each feature independently, both together, the retained null guard, rejected mismatches, startup opt-outs, and refusal of already-loaded images on both architectures. These tests do not exercise a live WeChat session.

## Inspected recall implementation

WeChat **4.1.15 / 270099** was inspected read-only. Both slices contain one matching predicate at a recorded function start:

| Architecture | Predicate virtual address | Modified instruction | Image UUID |
| --- | --- | --- | --- |
| arm64 | `0x48db554` | `0x48db560` | `ED4DCBD2-4896-3A6D-8A70-7D8D88F74B0D` |
| x86_64 | `0x5037860` | `0x503786b` | `97E21436-ABDA-3B79-BEC0-EF2653C6B423` |

Core: `Contents/Resources/wechat.dylib`.

SHA-256: `65117e24ca1a8b66aff122db2e86dbd544a8dc98064526440d1ffba690fd9a69`.

These addresses document the inspected file; the analyzer searches the instructions and does not use these addresses as fallbacks. Live recall prevention has been reported working on this build. This is not a verification of every architecture, message type, or synchronization path.

## Recall instruction recipes

Both supported predicates return whether a 32-bit field at message offset `0x0c` equals `10002` (`0x2712`). The preservation patch changes only the boolean-result instruction:

| Architecture | Exact original predicate bytes | Offset within predicate | Replacement bytes |
| --- | --- | --- | --- |
| arm64 | `080c40b949e284521f01096be0179f1ac0035fd6` | `12` | `00008052` |
| x86_64 | `554889e5817f0c122700000f94c05dc3` | `11` | `31c090` |

On arm64, `CSET W0, EQ` becomes `MOV W0, #0`. On Intel, `SETE AL` becomes `XOR EAX, EAX; NOP`. This patch keeps original loads, comparisons, function boundaries, and return instructions intact.

Read-only inspection of the four direct arm64 callers in build 270099 shows that this predicate selects system/revoke message extension construction and access. This supports the intended interception point but does not establish coverage of every deletion or synchronization path.

The ARM instruction write is aligned and its instruction cache is invalidated. The Intel replacement spans multiple instructions and is not atomic. On both architectures, changing page protection temporarily removes execution permission, so even an atomic ARM store is unsuitable for patching a core that may already be executing.

The plugin records the images already loaded when it initializes and refuses to patch any of those images (`late-image-refused`). It patches a matching newly loaded image only from the dyld image-add callback, before that image's initializers run. This is not a hot-patching mechanism. An app version that loads its core before the plugin initializes cannot activate through this path, even if static analysis succeeds. Disabling or enabling the plugin takes effect on the next process launch.

This also depends on the core being visible to dyld's image callbacks. A custom loader that maps the implementation without those callbacks is not covered. Read-only inspection of build 270099 places the primary-load call under the launcher's `_LdMain` entry, but its wrapper has multiple loading paths; other loading paths still require runtime verification.

## Extending compatibility

A future build with an unchanged, unique recall predicate may pass recall-only structural analysis without a new fixed-offset profile. Accessibility support requires a separately verified profile for the exact build and architecture. Test real behavior before relying on either feature. Missing or ambiguous matches require investigation; there is no nearest-version address fallback.

For a new recipe:

1. Inspect a clean app read-only and establish the candidate function's complete instruction semantics and callers.
2. Check every target architecture separately. A byte pattern must identify a complete supported predicate at a recorded function start, not a coincidental constant or a fragment inside another function.
3. Add the declarative profile and implement the corresponding native recipe validation. Changing `profiles.json` alone must not enable arbitrary address writes or arbitrary machine code.
4. Add tests for the new match, incorrect function boundaries, missing/duplicate matches, malformed Mach-O input, stale UUID/version plans, and rejected runtime recipes as appropriate.
5. Prepare a fresh copy, check startup diagnostics, and verify recall behavior with consent-based test messages, including restart persistence and relevant message types.

The existing implementation does not include the older Objective-C `MessageService` swizzling approach. Supporting 3.x requires a separately reviewed adapter, not relaxing the current signature checks.

## App copies, signing, and updates

Preparation never replaces the source installation or an existing output path. The copied launcher gains an `LC_LOAD_DYLIB` dependency on the local plugin; a generated plan lives under `Contents/Resources/WeChatTool/`. The source core image is not patched on disk. The native plugin changes only its own process memory after validation.

Each preparation allocates a random 32-character lowercase hexadecimal instance ID and assigns `local.wechattool.wechat.<instance ID>` as the copy’s bundle identifier. The metadata and generated plan record matching identities. Runtime validation checks those identities before activating in a prepared app. Each installation has its own sandbox, preferences, and application group; it starts with independent login and chat data. Preparation does not read or copy an account’s existing files.

The inspected native data-path code derives Documents, Application Support, and Caches from the main bundle’s container identity. Its application group derives from `TeamIdentifier + CFBundleIdentifier`. Preparation updates the corresponding signing entitlements and removes inherited access to the original application group. Native process and account file locks remain in place, scoped under each installation’s app-data directory. No process-count or account-lock bypass is installed. Distinct installations can run together; the GUI activates an already-running destination installation instead of requesting another instance of it.

The Share Sheet extension is omitted because its routing is tied to the official app. In isolated copies, the runtime also suppresses registration for the official Share Sheet notification channels, including when `WECHATTOOL_DISABLE=1` disables the selected feature patches. FileProvider is retained with a distinct extension identity and the new host’s document group and permissions. Inherited helpers keep the host’s sandbox; helpers with unhandled independent storage grants are refused. Prepared copies do not register the official app’s URL schemes.

Synthetic sandbox tests on Apple Silicon and Intel through Rosetta verify independent private and group storage, persisted preferences, inherited helper behavior, concurrent processes, and duplicate-process lock exclusion. Real WeChat copies have passed preparation and signature checks. Simultaneous login to real accounts has not been exercised; fixture results and static inspection do not establish complete live behavior.

Copying a prepared bundle in Finder preserves its identity and therefore its data. It is not a new installation. Use preparation separately for each account. Removing an app does not require deleting any account data.

Keep an independent chat backup before testing a modified client. The plugin does not manage chat backups or restore missing history.

Ad-hoc signing changes the app’s identity. Preparation preserves the sandbox and supported capabilities, replaces application and shared-storage identities, and adds `com.apple.security.cs.disable-library-validation` and `com.apple.security.cs.allow-unsigned-executable-memory`. It does not request a debugger entitlement or change SIP. Tencent’s publisher signature is not preserved, so operating-system permission checks, launch/login behavior, or other app features may still differ.

Preparation disables automatic update checks through the copied app’s updater metadata. Update the official app, then use it as the source for a new analysis and preparation. Every preparation creates a new identity and starts with independent data; the installer does not offer an in-place upgrade or data migration. Keep a previous installation while its local history is still needed. Do not copy an old plan into a new build or edit an identity to reuse its data.

## Diagnostics and disabling

The plugin reports compatibility and patch status through macOS unified logging, without message content or account identifiers. A successful status reports that the instruction changes were applied; it does not certify actual recall preservation or successful UI automation.

Start this command before launching the copied app:

```sh
log stream --style compact --level info --predicate 'subsystem == "local.wechattool"'
```

The log category is `runtime`. `initialized-active` or a later `active` entry indicates the hook was applied. `initialized-no-image-matched` means the plugin is waiting for a matching image. `initialized-refused` means a matching image was found but a validation or timing guard refused the patch. `late-image-refused` means the core was already loaded when the plugin initialized and was left unchanged.

The preferred startup disable switch is:

```sh
WECHATTOOL_DISABLE=1 /path/to/copied.app/Contents/MacOS/WeChat
```

It must be set for the copied executable's process. Setting it in a shell and using `open` may not propagate it through LaunchServices, so the command above launches the executable directly. Quit that installation first; other independently prepared installations can remain open.

The alternative resource marker is an empty file at `Contents/Resources/WeChatTool/disabled` inside the copied app, and is also startup-only. Adding it after preparation modifies the signed resource seal, so use the environment switch for normal troubleshooting. Rebuild and prepare a fresh copy to restore a clean, signed output after editing its resources.
