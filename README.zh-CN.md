# WeChatTool

[English](README.md) | 简体中文

[![CI](https://github.com/WeChatTool/WeChatTool/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/WeChatTool/WeChatTool/actions/workflows/ci.yml)

让撤回的微信消息，留在你的 Mac 上。WeChatTool 为 macOS 微信提供本地防撤回功能，配有简单易用的图形安装器，无需命令行，也无需自行编译。

支持多个微信账号同时使用，**每个安装副本登录一个账号，不同安装之间不共享数据**。各自的登录状态、聊天记录和设置独立保存，原版应用保持不变。

独立安装功能需要 **v0.1.1 或更高版本**的安装器。

## 使用 Mac 安装器

**[下载 WeChatTool-Installer v0.1.1](https://github.com/WeChatTool/WeChatTool/releases/download/v0.1.1/WeChatTool-Installer-0.1.1-universal2.zip)** · [发布说明与校验值](https://github.com/WeChatTool/WeChatTool/releases/tag/v0.1.1)

同一个通用 ZIP 压缩包支持运行 **macOS 14 或更高版本的 Apple Silicon 和 Intel Mac**。安装器已内置所需组件，无需安装 Python、Xcode 或开发工具。标为 **Source code** 的压缩包仅包含源代码，不包含安装器应用。

你需要：

- 一台运行 **macOS 14 或更高版本**的 Apple Silicon 或 Intel Mac。可在 **苹果菜单 → 关于本机** 中查看 macOS 版本。
- 已安装官方微信 4.x，通常位于“应用程序”文件夹。
- 足够存放一份微信应用副本的可用磁盘空间。

### 1. 打开安装器

双击下载的 ZIP 压缩包将其解压，然后打开 **WeChatTool-Installer.app**。界面会根据 Mac 的首选语言显示英文或简体中文。

此版本采用 **ad-hoc 签名，尚未经过 Apple 公证**。如果 macOS 阻止打开，且你信任下载来源，请在尝试启动后前往 **系统设置 → 隐私与安全性 → 仍要打开**。详见 [Apple 关于打开身份不明开发者应用的说明](https://support.apple.com/zh-cn/guide/mac-help/mh40616/mac)。

### 2. 选择微信并检查兼容性

安装器默认选择 `/Applications/WeChat.app`。如果官方微信安装在其他位置，请点击 **浏览**。请始终选择未经修改的官方应用，不要选择已经处理过的副本。

点击 **检查兼容性**。此操作只读取应用，不会修改微信或聊天数据。如果该版本不受支持，请继续使用原版应用。

### 3. 创建应用副本

点击 **创建微信副本**，为此账号的应用选择名称和保存位置。保存对话框默认填入文件名 `WeChatTool-WeChat.app`，位置为个人文件夹中的 `Applications`。创建前可以修改文件名。其他微信应用可以保持运行。

如果该应用已经存在，请换一个名称。安装器不会覆盖已有应用或文件夹。等待创建完成；此过程会复制应用并在本机签名。

**每次新建安装都使用独立数据。** 请单独登录；原版应用或其他副本的聊天记录和设置不会自动导入。重要聊天请在其所属的安装副本中做好备份。

### 4. 打开并验证

创建成功后，点击 **打开微信**，或点击 **在访达中显示** 找到副本，登录要在此安装中使用的账号。如果此副本已在运行，点击 **打开微信** 会显示已有窗口。

首次测试时，请让另一个账号发送一条新的文字消息，然后撤回。检查原消息是否仍然可见，并重新打开会话确认。也请确认能正常收发消息，并在重新打开此副本后查看其中的新聊天记录。

需要启用插件时，请使用这个应用副本。打开“应用程序”文件夹中的原版 **WeChat.app**，则会运行官方安装版本。

## 同时运行多个微信

1. 打开安装器，选择同一个未经修改的官方微信应用。
2. 分别点击 **创建微信副本**，为每个账号创建一个名称不同的安装副本，例如 **WeChat01.app**、**WeChat02.app**。
3. 打开这些副本，在每个副本中登录不同账号，即可同时使用。官方微信也可以保持运行。

每个安装副本运行一个微信实例，重复打开同一副本会显示已有窗口。**不同安装之间不共享登录状态、聊天记录或设置**，新副本需要单独登录。

**请通过安装器分别创建每个安装副本。** 在访达中复制已生成的应用会保留其身份和数据，不会得到独立安装。旧版安装器创建的副本也不会自动转换。

## 兼容性

微信 **4.1.15（构建版本 270099）** 的 Apple Silicon 和 Intel 版本均已通过兼容性检查。原生代码测试已在 Apple Silicon 上运行，Intel 架构的测试则通过 Rosetta 运行。这不代表所有微信功能、消息类型或未来版本都能正常使用。

独立存储已通过两种处理器架构的模拟沙盒应用测试，真实微信副本也已通过创建和签名检查。**尚未测试多个真实账号同时登录。**

每次创建副本前，都应先检查已安装的微信版本，再通过测试消息验证实际效果。不支持微信 3.x、Windows 版或移动端。

## 更新

微信更新后，请打开安装器，选择更新后的官方应用，重新检查兼容性。使用新的名称创建副本，例如 **WeChatTool-WeChat-new.app**。不要将旧副本的配置用于新版微信。

新副本始终是独立安装，即使用于更新，也需要单独登录并使用独立数据。安装器不支持原位升级，也不会迁移旧副本的聊天记录。请保留仍需使用的旧应用及其数据。生成的应用已在配置中关闭自动更新检查；请更新官方应用后，再创建新副本。

更新 WeChatTool 时，请从 [Releases](https://github.com/WeChatTool/WeChatTool/releases) 下载适合这台 Mac 的最新安装器，并**重新创建微信副本**。已有副本不会自动更新插件。

## 返回原版或卸载

从“应用程序”文件夹打开原版 **WeChat.app**，即可使用官方安装及其自身数据。如果要移除某个安装副本，请先退出该副本，再在访达中**仅将其应用**移到废纸篓。不再需要安装器时，也可以删除 **WeChatTool-Installer.app**。

请勿删除微信的聊天数据文件夹或容器目录。

## 常见问题

| 问题 | 处理方法 |
| --- | --- |
| macOS 无法打开安装器 | 确认系统为 macOS 14 或更高版本。如果系统阻止打开，且你信任下载来源，请按照上文的**仍要打开**步骤操作。 |
| 兼容性检查未通过 | 使用原版应用。当前版本的工具无法为该微信安装创建兼容的副本。 |
| 目标路径已存在 | 选择一个以 `.app` 结尾的新名称，或在访达中仅删除旧的生成副本。 |
| 新副本没有显示原来的聊天记录 | 这是正常现象：每个安装的数据独立保存。请打开原来使用的应用查看其中的聊天记录。 |
| 第二个副本打开了相同账号或数据 | 请通过安装器单独创建。访达复制的应用仍保留第一个副本的身份。 |
| 副本无法启动或登录 | 排查期间可以使用原版应用。不要通过删除数据库或重置账号数据来排查问题。 |
| 消息仍被撤回 | 确认打开的是生成的副本。可按下方可选步骤查看运行日志。 |
| 更新后插件失效 | 检查更新后的官方应用，并重新创建副本。 |

反馈问题时，请提供 macOS 版本、处理器类型、微信版本及构建版本，以及安装器显示的兼容性结果或错误信息。分享诊断输出前，请移除个人路径和标识信息。

## 从源代码构建（可选）

本节适用于希望自行构建插件的用户。你需要 **Python 3.10 或更高版本**，以及包含 `clang`、`make` 和 `codesign` 的 **Xcode 命令行工具**。以下命令行流程无需安装额外的 Python 包、使用管理员权限或修改系统完整性保护（SIP）。

检查开发工具：

```sh
python3 --version
xcode-select -p
```

如果尚未安装命令行工具，运行以下命令并等待安装完成：

```sh
xcode-select --install
```

下载源代码，然后在其目录中运行后续命令：

```sh
git clone https://github.com/WeChatTool/WeChatTool.git
cd WeChatTool
```

检查官方微信安装：

```sh
make analyze
```

查看结果中是否出现 `structurally-compatible`。这表示已安装的微信通过了检查，但尚未验证实际的消息行为。如果结果为 `unsupported`，请停止操作，继续使用原版应用。

构建插件并创建独立副本：

```sh
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat.app"
```

成功时会输出 `prepared-and-signature-verified`。每次运行 `prepare` 都会创建独立安装。目标必须是尚不存在、以 `.app` 结尾的路径；已有文件和文件夹不会被覆盖。此操作不会启动微信，也不会复制聊天数据。

打开副本，并按照上文步骤验证：

```sh
open "$HOME/Applications/WeChatTool-WeChat.app"
```

开发时也可以运行 `make prepare`，将副本创建在 `build/prepared.noindex/WeChatTool-WeChat.app`。`.noindex` 文件夹可避免此测试副本出现在 Spotlight 搜索中。请使用以下命令直接打开：

```sh
open build/prepared.noindex/WeChatTool-WeChat.app
```

如果官方微信安装在其他位置，请在以下两个命令中指定相同的源路径：

```sh
python3 -m wechattool analyze --app "$HOME/Applications/WeChat.app"
python3 -m wechattool prepare \
  --app "$HOME/Applications/WeChat.app" \
  --output "$HOME/Applications/WeChatTool-WeChat.app"
```

如果本地仓库没有未提交的修改，可以这样更新 WeChatTool 并创建新副本：

```sh
git pull --ff-only
make analyze
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat-new.app"
```

维护者可参阅 [发布说明](docs/distribution.md)，了解如何打包独立安装器。

## 可选的命令行诊断

如需查看运行状态，请在打开副本**之前**，先在终端运行：

```sh
log stream --style compact --level info --predicate 'subsystem == "local.wechattool"'
```

- `active`：插件已启用。请用测试消息确认实际效果。
- `initialized-no-image-matched`：插件正在启动过程中等待加载。仅出现此状态并不表示插件已成功启用。
- `late-image-refused`、`initialized-refused` 或匹配失败状态：插件在本次启动中未能启用。请返回原版应用，重新检查兼容性。
- `disabled`：插件在本次启动中已被禁用。

按 **Control-C** 停止查看日志。

如需在某个安装副本中临时禁用插件，请退出该副本后运行：

```sh
WECHATTOOL_DISABLE=1 "$HOME/Applications/WeChatTool-WeChat.app/Contents/MacOS/WeChat"
```

此操作仅在本次启动中禁用插件。该安装仍保留独立的数据和签名身份。

## 限制与隐私

- 插件只影响这台 Mac 上的显示，不会改变其他会话参与者或设备看到的内容。
- 你自己撤回的消息，以及从其他设备撤回的消息，也可能继续在本机显示。
- 插件无法恢复已经删除的消息，也无法恢复从未下载或已经不可用的媒体文件。
- 某些消息类型或微信功能的行为可能发生变化，不保证兼容未来版本。
- 生成的副本不包含微信的 macOS 系统分享扩展。请在副本内发送文件。FileProvider 功能仍保留，并使用与其他安装独立的存储。
- 插件不会直接打开聊天数据库，不读取账号凭据，也不导出聊天数据。插件日志仅包含启用情况和兼容性状态，不包含姓名、会话标识或消息文字。
- WeChatTool 是独立项目。生成的应用使用本机签名，不使用微信原发布者的签名身份。

## 许可证

MIT。适用的许可条款请参阅 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
