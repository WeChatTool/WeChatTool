# WeChatTool

English | [简体中文](README.zh-CN.md)

Keep recalled WeChat messages on your Mac. WeChatTool adds local anti-recall protection with a simple graphical installer. No command line or compiling required.

Use different WeChat accounts side by side, with **one account per installation**. Each copy has separate chats, login, and settings. Your original app remains unchanged.

**Development update:** Independent installations require the new **0.1.1** installer build. The published **v0.1.0** download below does **not** include this feature. Version 0.1.1 has not been published yet; [build the current installer from source](docs/distribution.md) to use the multi-account instructions below.

## Install with the Mac app

**[Download WeChatTool-Installer v0.1.0](https://github.com/WeChatTool/WeChatTool/releases/download/v0.1.0/WeChatTool-Installer-0.1.0-universal2.zip)** · [Release notes and checksum](https://github.com/WeChatTool/WeChatTool/releases/tag/v0.1.0)

One Universal ZIP supports **Apple Silicon and Intel Macs running macOS 14 or later**. The installer includes everything it needs; you do not need Python, Xcode, or developer tools. The **Source code** archives contain code only, not the installer app.

You need:

- An Apple Silicon or Intel Mac running **macOS 14 or later**. Check your macOS version in **Apple menu → About This Mac**.
- An official WeChat 4.x installation, normally in **Applications**.
- Enough free disk space for another copy of WeChat.

### 1. Open the installer

Double-click the downloaded ZIP to extract it, then open **WeChatTool-Installer.app**. The interface follows your Mac’s preferred language, with English and Simplified Chinese available.

This release is **ad-hoc signed and not notarized by Apple**. If macOS blocks it and you trust the download, open **System Settings → Privacy & Security → Open Anyway** after attempting to launch it. See [Apple’s instructions for opening an app from an unidentified developer](https://support.apple.com/en-sg/guide/mac-help/mh40616/mac).

### 2. Select WeChat and check compatibility

The installer selects `/Applications/WeChat.app` by default. Use **Browse** if your official app is somewhere else. Always select an unmodified official installation, not a previously prepared copy.

Click **Check Compatibility**. This only reads the application; it does not modify WeChat or your chats. If the installation is unsupported, continue using the original app.

### 3. Create your app copy

Click **Create WeChat Copy** and choose a name and location for this account’s app. The default is `Applications/WeChatTool-WeChat.app` inside your home folder. Other WeChat installations can stay open.

Choose a new name if that app already exists. The installer does not overwrite an existing app or folder. Wait for preparation to finish; it creates and locally signs the copy.

**Every newly created installation starts with its own data.** Sign in separately; chats and settings from the original app or another copy are not automatically imported. Keep important chats backed up in the installation where they belong.

### 4. Open and verify

After preparation succeeds, choose **Open WeChat**, or **Show in Finder** to locate your copy. Sign in to the account you want to use in this installation. If this copy is already running, **Open WeChat** brings its window forward.

For a first test, have another account send a new text message, then recall it. Check that the original message remains visible, including after reopening the conversation. Also verify normal messaging and that this installation’s new chats remain available after reopening it.

Use this copied app whenever you want the plugin enabled. Opening the original **WeChat.app** in Applications runs the official installation.

## Use another account

Repeat **Create WeChat Copy** from the same clean official app and choose a different name, such as **WeChat-Work.app** or **WeChat-Personal.app**. Sign in to a different account in each installation. They can run alongside one another and the official app, with separate chat data, settings, and login state.

**Create each installation with the installer.** Duplicating a prepared app in Finder keeps its identity and data; it does not create an independent installation. Copies made with an older installer are not converted automatically.

## Compatibility

WeChat **4.1.15 (build 270099)** has passed the compatibility check for Apple Silicon and Intel. Native tests have run on Apple Silicon and for the Intel build through Rosetta. This is not a guarantee that every WeChat feature, message type, or future release will work.

Independent storage has passed synthetic sandbox tests on both processors, and real WeChat copies have passed preparation and signature checks. **Simultaneous sign-in to real accounts has not yet been tested.**

Check your installed version before creating each copy, then verify the feature with a test message. WeChat 3.x, Windows, and mobile clients are not supported.

## Updating

After WeChat updates, open the installer, select the updated official app, and check compatibility again. Create a fresh copy with a new name, such as **WeChatTool-WeChat-new.app**. Do not reuse an old copy’s configuration with a newer WeChat version.

A fresh copy is a new installation with separate login and data, including when created for an update. The installer does not perform an in-place upgrade or move the previous installation’s chats. Keep the previous app and its data until you no longer need them. Automatic update checks are disabled in the prepared app’s settings; update the official app and prepare a new copy instead.

To update WeChatTool, download the latest compatible installer from [Releases](https://github.com/WeChatTool/WeChatTool/releases) and **create a fresh WeChat copy**. Existing copies do not receive plugin updates automatically.

## Return to the original app or uninstall

Open the original **WeChat.app** from Applications to use the official installation and its own data. To remove a prepared installation, quit that copy and move **only its app** to Trash using Finder. You can also remove **WeChatTool-Installer.app** when you no longer need it.

Do not delete WeChat’s chat-data folders or containers.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| macOS will not open the installer | Confirm that you have macOS 14 or later. If the app is blocked and you trust the download, follow the **Open Anyway** steps above. |
| Compatibility check fails | Use the original app. This build cannot prepare a compatible copy of that installation. |
| The destination already exists | Choose a new name ending in `.app`, or use Finder to remove only an old generated app. |
| A new copy does not show my original chats | This is expected: each installation has independent data. Open the app where those chats were created to access them. |
| A second copy opens the same account or data | Create it separately with the installer. Finder duplicates retain the first copy’s identity. |
| The copied app will not launch or sign in | Use the original app while investigating. Do not delete databases or reset account data to troubleshoot. |
| Messages are still recalled | Confirm that you opened the prepared copy. Optional runtime diagnostics are below. |
| The plugin stopped working after an update | Check the updated official app and prepare a new copy. |

When reporting a problem, include your macOS version, processor, WeChat version/build, and the compatibility result or error shown by the installer. Remove personal paths and identifiers before sharing diagnostic output.

## Build from source (optional)

This section is for users who prefer to build the plugin themselves. You need **Python 3.10 or newer** and **Xcode Command Line Tools**, including `clang`, `make`, and `codesign`. No additional Python packages, administrator privileges, or changes to System Integrity Protection are required for this command-line workflow.

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

Build the plugin and prepare an independent copy:

```sh
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat.app"
```

Success is reported as `prepared-and-signature-verified`. Each `prepare` command creates an independent installation. The destination must be a new path ending in `.app`; existing files and folders are never overwritten. Preparation does not launch WeChat or copy chat data.

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

To temporarily disable the plugin in one installation, quit that copy, then run:

```sh
WECHATTOOL_DISABLE=1 "$HOME/Applications/WeChatTool-WeChat.app/Contents/MacOS/WeChat"
```

This disables the plugin for that launch only. The installation keeps its separate data and signing identity.

## Limits and privacy

- The effect is local to this Mac. It does not change what other participants or devices see.
- Your own recalls and recalls from your other devices may also remain visible locally.
- The plugin cannot recover messages that were already removed, or media that was never downloaded or is no longer available.
- Some message types or WeChat features may behave differently. Compatibility with future releases is not guaranteed.
- Prepared copies omit WeChat’s macOS Share Sheet extension. Share files from inside the copied app instead. FileProvider support is retained with storage separate from other installations.
- The plugin does not directly open chat databases, read account credentials, or export chat data. Plugin logs contain activation and compatibility status without names, conversation identifiers, or message text.
- WeChatTool is an independent project. The prepared app is signed locally, not with WeChat’s original publisher identity.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for the applicable license terms.
