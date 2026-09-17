# WeChatTool

English | [简体中文](README.zh-CN.md)

A macOS WeChat plugin that helps keep received messages visible when the sender recalls them.

WeChatTool builds a separate plugin-enabled copy of WeChat. Your original app remains available, and you can return to it at any time. The repository contains source code; the plugin and app copy are built locally.

## Requirements

- A Mac with Apple Silicon or an Intel processor.
- WeChat 4.x installed, normally at `/Applications/WeChat.app`.
- Python 3.10 or newer, available as `python3`.
- Xcode Command Line Tools, including `clang`, `make`, and `codesign`.
- Enough disk space for another copy of the WeChat app.

Check your tools:

```sh
python3 --version
xcode-select -p
```

If the Command Line Tools are missing, install them and wait for installation to finish:

```sh
xcode-select --install
```

No Python packages, administrator privileges, or changes to System Integrity Protection are required.

## Compatibility

WeChat **4.1.15 (build 270099)** has passed the compatibility check for both architectures. Native tests have run on Apple Silicon and for the Intel build through Rosetta. This is not a guarantee that every WeChat feature, message type, or future release will work.

Always check your installed version before preparing a copy. WeChat 3.x, Windows, and mobile clients are not supported.

## Get started

### 1. Download the source

```sh
git clone https://github.com/WeChatTool/WeChatTool.git
cd WeChatTool
```

Run the commands below from this directory.

### 2. Check your WeChat installation

```sh
make analyze
```

Look for `structurally-compatible`. This means the installation passed the compatibility checks; you still need to confirm the feature after launching the prepared copy. If the result is `unsupported`, stop and use the original app.

This command only reads the installed application. It does not modify WeChat or your chats.

### 3. Build and prepare the app

```sh
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat.app"
```

Preparation creates and locally signs the app copy. Success is reported as `prepared-and-signature-verified`; it does not launch the app.

The destination must be a new path ending in `.app`. Existing files and folders are never overwritten. If that destination already exists, choose another name or remove only the old generated app through Finder before trying again.

### 4. Launch and verify

Keep a separate backup of important chats before trying a modified client. **Quit the original WeChat before opening the prepared copy.** The two apps may use the same chat storage; never run them together. A copied application is not a backup of your chats, and local signing can affect access to existing data or macOS permissions.

```sh
open "$HOME/Applications/WeChatTool-WeChat.app"
```

Log in normally. For a first test, have another account send a new text message, then recall it. Check that it remains visible after reopening the conversation. Also verify normal messaging and access to your existing history before relying on the plugin.

Use this copied app whenever you want the plugin enabled. Launching `/Applications/WeChat.app` runs the original installation.

## Other installation paths

If WeChat is installed elsewhere, pass the same source path to both commands:

```sh
python3 -m wechattool analyze --app "$HOME/Applications/WeChat.app"
python3 -m wechattool prepare \
  --app "$HOME/Applications/WeChat.app" \
  --output "$HOME/Applications/WeChatTool-WeChat.app"
```

Always prepare from a clean official installation, not from a previously prepared copy.

For a quick local build using the default source and a destination inside this repository:

```sh
make prepare
open build/WeChatTool-WeChat.app
```

This creates `build/WeChatTool-WeChat.app`; the same requirement to quit the original app first applies.

## Updating

After WeChat updates, run the compatibility check against the updated official app and prepare a fresh copy with a new output name. Do not reuse an old copy's configuration with a newer WeChat version.

To update WeChatTool from a clean checkout:

```sh
git pull --ff-only
make analyze
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat-new.app"
```

Quit the previous copy before launching the new one. Keep the previous app until you have checked the new copy. WeChat's automatic updates are not disabled by this tool; an update to the prepared app can remove or disable the plugin.

## Temporarily disable the plugin

Quit WeChat, then start the copied executable directly with the disable flag:

```sh
WECHATTOOL_DISABLE=1 "$HOME/Applications/WeChatTool-WeChat.app/Contents/MacOS/WeChat"
```

This disables the plugin for that launch only. It does not restore the copied app's original signing identity. To use the original installation instead, quit the copy and run:

```sh
open /Applications/WeChat.app
```

## Remove the plugin

Quit the prepared app and move **only that app copy** to Trash using Finder. Continue using the original WeChat installation. Do not delete WeChat's chat-data folders or containers.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `python3` is missing or too old | Install Python 3.10 or newer and check `python3 --version` again. |
| Build tools are missing | Run `xcode-select --install`, finish installation, then retry `make build`. |
| Compatibility result is `unsupported` | Use the original app. This build cannot prepare a compatible copy of that installation. |
| The destination already exists | Choose a new `.app` output path. |
| The copied app will not launch, sign in, or show existing chats | Quit it and return to the original app. Do not delete databases or reset account data to troubleshoot. |
| Messages are still recalled | Confirm that you launched the prepared copy, then check its runtime log below. |
| The plugin stopped working after an update | Check the updated official app and prepare a new copy. |

To check runtime status, run this in Terminal **before** launching the prepared copy:

```sh
log stream --style compact --level info --predicate 'subsystem == "local.wechattool"'
```

- `active`: the plugin activated. Confirm the result with a test message.
- `initialized-no-image-matched`: the plugin is waiting during startup. This alone does not mean activation succeeded.
- `late-image-refused`, `initialized-refused`, or a mismatch status: the plugin could not activate for this launch. Return to the original app and check compatibility again.
- `disabled`: the plugin was disabled for this launch.

Press **Control-C** to stop viewing logs. When reporting a problem, include your macOS version, processor, WeChat version/build, compatibility result, and relevant plugin status. Remove personal paths and identifiers before sharing diagnostic output.

## Limits and privacy

- The effect is local to this Mac. It does not change what other participants or devices see.
- Your own recalls and recalls from your other devices may also remain visible locally.
- The plugin cannot recover messages that were already removed, or media that was never downloaded or is no longer available.
- Some message types or WeChat features may behave differently. Compatibility with future releases is not guaranteed.
- The plugin does not read or export chat databases, account credentials, or message contents. Its logs contain activation and compatibility status, not conversations.
- WeChatTool is an independent project. The prepared app is signed locally, not with WeChat's original publisher identity.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for the applicable license terms.
