# WeChatTool

English | [简体中文](README.zh-CN.md)

A macOS WeChat plugin that helps keep received messages visible when the sender recalls them.

WeChatTool creates a separate plugin-enabled copy of WeChat. Your original app remains unchanged and available to use.

## Install with the Mac app

**Packaged builds need no Terminal commands, Python, Xcode, or compiling.** When a packaged build is available, download **WeChatTool Installer** from [Releases](https://github.com/WeChatTool/WeChatTool/releases). The **Source code** archives contain code only, not the installer app.

You need:

- A Mac and an installer build that supports its macOS version and processor. Choose Apple Silicon or Intel as appropriate; a Universal build supports both. Find your processor and macOS version in **Apple menu → About This Mac**.
- An official WeChat 4.x installation, normally in **Applications**.
- Enough free disk space for another copy of WeChat.

### 1. Open the installer

Open the downloaded archive or disk image, then open **WeChatTool Installer.app**. The interface follows your Mac’s preferred language, with English and Simplified Chinese available.

### 2. Select WeChat and check compatibility

The installer selects `/Applications/WeChat.app` by default. Use **Browse** if your official app is somewhere else. Always select an unmodified official installation, not a previously prepared copy.

Click **Check Compatibility**. This only reads the application; it does not modify WeChat or your chats. If the installation is unsupported, continue using the original app.

### 3. Create your app copy

Back up important chats separately and **quit WeChat normally before creating the copy**. Click **Create WeChat Copy** and choose where to save it. The default is `Applications/WeChatTool-WeChat.app` inside your home folder.

Choose a new name if that app already exists. The installer does not overwrite an existing app or folder. Wait for preparation to finish; it creates and locally signs the copy.

The two apps may use the same chat storage. **Do not run them together.** An application copy is not a backup of your chats, and local signing can affect access to existing data or macOS permissions.

### 4. Open and verify

After preparation succeeds, choose **Open WeChat**, or **Show in Finder** to locate your copy. Log in normally.

For a first test, have another account send a new text message, then recall it. Check that it remains visible after reopening the conversation. Also verify normal messaging and access to your existing history before relying on the plugin.

Use this copied app whenever you want the plugin enabled. Opening the original **WeChat.app** in Applications runs the official installation.

## Compatibility

WeChat **4.1.15 (build 270099)** has passed the compatibility check for Apple Silicon and Intel. Native tests have run on Apple Silicon and for the Intel build through Rosetta. This is not a guarantee that every WeChat feature, message type, or future release will work.

Check your installed version before creating each copy, then verify the feature with a test message. WeChat 3.x, Windows, and mobile clients are not supported.

## Updating

After WeChat updates, open the installer, select the updated official app, and check compatibility again. Create a fresh copy with a new name, such as **WeChatTool-WeChat-new.app**. Do not reuse an old copy’s configuration with a newer WeChat version.

Quit the previous copy before opening the new one. Keep the previous app until you have checked the new copy. WeChat’s automatic updates are not disabled by this tool; an update to the prepared app can remove or disable the plugin.

To update the installer itself, download a newer compatible packaged build when one is available on [Releases](https://github.com/WeChatTool/WeChatTool/releases).

## Return to the original app or uninstall

Quit the prepared copy, then open the original **WeChat.app** from Applications. To remove the plugin, move **only the prepared app copy** to Trash using Finder. You can also remove **WeChatTool Installer.app** when you no longer need it.

Do not delete WeChat’s chat-data folders or containers.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| There is no installer download | A packaged release may not be available yet. Building the source requires the developer tools listed below. |
| macOS will not open the installer | Check that the download supports your Mac and follow the instructions for that release. |
| Compatibility check fails | Use the original app. This build cannot prepare a compatible copy of that installation. |
| The destination already exists | Choose a new name ending in `.app`, or use Finder to remove only an old generated app. |
| The copied app will not launch, sign in, or show existing chats | Quit it and return to the original app. Do not delete databases or reset account data to troubleshoot. |
| Messages are still recalled | Confirm that you opened the prepared copy. Optional runtime diagnostics are below. |
| The plugin stopped working after an update | Check the updated official app and prepare a new copy. |

When reporting a problem, include your macOS version, processor, WeChat version/build, and the compatibility result or error shown by the installer. Remove personal paths and identifiers before sharing diagnostic output.

## Build from source (optional)

This section is for users who prefer the command line or cannot obtain a packaged build. You need **Python 3.10 or newer** and **Xcode Command Line Tools**, including `clang`, `make`, and `codesign`. No additional Python packages, administrator privileges, or changes to System Integrity Protection are required for this command-line workflow.

Check your tools:

```sh
python3 --version
xcode-select -p
```

If the Command Line Tools are missing, install them and wait for installation to finish:

```sh
xcode-select --install
```

Download the source, then run the remaining commands from its directory:

```sh
git clone https://github.com/WeChatTool/WeChatTool.git
cd WeChatTool
```

Check the official WeChat installation:

```sh
make analyze
```

Look for `structurally-compatible`. This means the installation passed the checks; it does not verify live message behavior. If the result is `unsupported`, stop and use the original app.

Quit WeChat, then build the plugin and prepare a copy:

```sh
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat.app"
```

Success is reported as `prepared-and-signature-verified`. The destination must be a new path ending in `.app`; existing files and folders are never overwritten. Preparation does not launch WeChat.

Open the copy and follow the verification steps above:

```sh
open "$HOME/Applications/WeChatTool-WeChat.app"
```

If the official app is installed elsewhere, pass the same source path to both commands:

```sh
python3 -m wechattool analyze --app "$HOME/Applications/WeChat.app"
python3 -m wechattool prepare \
  --app "$HOME/Applications/WeChat.app" \
  --output "$HOME/Applications/WeChatTool-WeChat.app"
```

To update WeChatTool from a clean checkout and create a fresh copy:

```sh
git pull --ff-only
make analyze
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat-new.app"
```

Maintainers can find instructions for packaging the standalone installer in [Distribution](docs/distribution.md).

## Optional command-line diagnostics

To check runtime status, run this in Terminal **before** opening the prepared copy:

```sh
log stream --style compact --level info --predicate 'subsystem == "local.wechattool"'
```

- `active`: the plugin activated. Confirm the result with a test message.
- `initialized-no-image-matched`: the plugin is waiting during startup. This alone does not mean activation succeeded.
- `late-image-refused`, `initialized-refused`, or a mismatch status: the plugin could not activate for this launch. Return to the original app and check compatibility again.
- `disabled`: the plugin was disabled for this launch.

Press **Control-C** to stop viewing logs.

To temporarily disable the plugin in the copied app, quit WeChat, then run:

```sh
WECHATTOOL_DISABLE=1 "$HOME/Applications/WeChatTool-WeChat.app/Contents/MacOS/WeChat"
```

This disables the plugin for that launch only. It does not restore the copied app’s original signing identity.

## Limits and privacy

- The effect is local to this Mac. It does not change what other participants or devices see.
- Your own recalls and recalls from your other devices may also remain visible locally.
- The plugin cannot recover messages that were already removed, or media that was never downloaded or is no longer available.
- Some message types or WeChat features may behave differently. Compatibility with future releases is not guaranteed.
- The plugin does not read or export chat databases, account credentials, or message contents. Its logs contain activation and compatibility status, not conversations.
- WeChatTool is an independent project. The prepared app is signed locally, not with WeChat’s original publisher identity.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for the applicable license terms.
