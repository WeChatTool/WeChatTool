# WeChatTool

[English](README.md) | 简体中文

适用于 macOS 微信的插件，让收到的消息在发送者撤回后仍可在本机显示。

WeChatTool 会创建一个启用插件的微信应用副本。原版应用仍可使用，你可以随时切换回原版。本仓库提供源代码，插件和应用副本均在本机构建。

## 环境要求

- 配备 Apple Silicon 或 Intel 处理器的 Mac。
- 已安装微信 4.x，默认路径为 `/Applications/WeChat.app`。
- Python 3.10 或更高版本，且可通过 `python3` 命令运行。
- Xcode 命令行工具，包含 `clang`、`make` 和 `codesign`。
- 足够存放一份微信应用副本的磁盘空间。

检查开发工具：

```sh
python3 --version
xcode-select -p
```

如果尚未安装命令行工具，运行以下命令并等待安装完成：

```sh
xcode-select --install
```

无需安装额外的 Python 包、使用管理员权限或修改系统完整性保护（SIP）。

## 兼容性

微信 **4.1.15（构建版本 270099）** 的两个架构版本均已通过兼容性检查。原生代码测试已在 Apple Silicon 上运行，Intel 架构的测试则通过 Rosetta 运行。这不代表所有微信功能、消息类型或未来版本都能正常使用。

每次创建副本前，都应先检查已安装的微信版本。不支持微信 3.x、Windows 版或移动端。

## 开始使用

### 1. 下载源代码

```sh
git clone https://github.com/WeChatTool/WeChatTool.git
cd WeChatTool
```

以下命令均在该目录中运行。

### 2. 检查已安装的微信

```sh
make analyze
```

查看结果中是否出现 `structurally-compatible`。这表示已安装的微信通过了兼容性检查；启动生成的副本后，仍需实际验证插件功能。如果结果为 `unsupported`，请停止操作，继续使用原版应用。

此命令只读取已安装的应用，不会修改微信或聊天数据。

### 3. 构建插件并创建应用副本

```sh
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat.app"
```

此步骤会创建应用副本并在本机签名。成功时会输出 `prepared-and-signature-verified`，但不会自动启动应用。

目标路径必须是尚不存在、以 `.app` 结尾的路径。已有文件和文件夹不会被覆盖。如果目标已存在，请换一个名称，或先在访达中仅移除旧的生成副本，再重试。

### 4. 启动并验证

使用修改过的客户端前，请单独备份重要的聊天数据。**打开副本前，务必先退出原版微信。** 两个应用可能使用同一份聊天数据，请勿同时运行。应用副本并不是聊天数据备份，本机签名也可能影响对已有数据的访问或 macOS 权限。

```sh
open "$HOME/Applications/WeChatTool-WeChat.app"
```

正常登录。首次测试时，请让另一个账号发送一条新的文字消息，然后撤回。重新打开会话，检查消息是否仍然可见。在正式使用插件前，也请确认正常收发消息和查看已有聊天记录等功能。

需要启用插件时，请使用这个应用副本。启动 `/Applications/WeChat.app` 则会运行原版微信。

## 其他安装路径

如果微信安装在其他位置，请在以下两个命令中指定相同的原版应用路径：

```sh
python3 -m wechattool analyze --app "$HOME/Applications/WeChat.app"
python3 -m wechattool prepare \
  --app "$HOME/Applications/WeChat.app" \
  --output "$HOME/Applications/WeChatTool-WeChat.app"
```

请始终以未经修改的官方应用为源文件，不要使用已经处理过的副本。

如需使用默认源路径，并将副本生成到本仓库目录中，可以运行：

```sh
make prepare
open build/WeChatTool-WeChat.app
```

这会创建 `build/WeChatTool-WeChat.app`。启动前同样需要先退出原版微信。

## 更新

微信更新后，请对更新后的官方应用重新运行兼容性检查，并使用新的输出名称创建副本。不要将旧副本的配置用于新版微信。

如果本地仓库没有未提交的修改，可以这样更新 WeChatTool：

```sh
git pull --ff-only
make analyze
make build
python3 -m wechattool prepare --output "$HOME/Applications/WeChatTool-WeChat-new.app"
```

启动新副本前，请先退出旧副本。在确认新副本正常工作之前，保留旧副本。此工具不会禁用微信的自动更新；生成的应用副本一旦更新，插件可能被移除或失效。

## 临时禁用插件

退出微信，然后设置禁用标志，直接运行副本中的可执行文件：

```sh
WECHATTOOL_DISABLE=1 "$HOME/Applications/WeChatTool-WeChat.app/Contents/MacOS/WeChat"
```

此操作仅在本次启动中禁用插件，不会恢复副本原有的签名身份。如果要使用原版应用，请退出副本后运行：

```sh
open /Applications/WeChat.app
```

## 移除插件

退出生成的应用，然后使用访达将**该应用副本**移到废纸篓，继续使用原版微信即可。请勿删除微信的聊天数据文件夹或容器目录。

## 常见问题

| 问题 | 处理方法 |
| --- | --- |
| 缺少 `python3`，或版本过低 | 安装 Python 3.10 或更高版本，再运行 `python3 --version` 确认。 |
| 缺少构建工具 | 运行 `xcode-select --install`，完成安装后重试 `make build`。 |
| 兼容性检查结果为 `unsupported` | 使用原版应用。当前版本的工具无法为该微信安装创建兼容的副本。 |
| 目标路径已存在 | 选择一个新的 `.app` 输出路径。 |
| 副本无法启动、登录或显示已有聊天记录 | 退出副本并返回原版应用。不要通过删除数据库或重置账号数据来排查问题。 |
| 消息仍被撤回 | 确认启动的是生成的副本，然后按下方说明查看运行日志。 |
| 更新后插件失效 | 检查更新后的官方应用，并重新创建副本。 |

如需查看运行状态，请在启动副本**之前**，先在终端运行：

```sh
log stream --style compact --level info --predicate 'subsystem == "local.wechattool"'
```

- `active`：插件已启用。请用测试消息确认实际效果。
- `initialized-no-image-matched`：插件正在启动过程中等待加载。仅出现此状态并不表示插件已成功启用。
- `late-image-refused`、`initialized-refused` 或匹配失败状态：插件在本次启动中未能启用。请返回原版应用，重新检查兼容性。
- `disabled`：插件在本次启动中已被禁用。

按 **Control-C** 停止查看日志。反馈问题时，请提供 macOS 版本、处理器类型、微信版本及构建版本、兼容性检查结果，以及相关插件状态。分享诊断输出前，请移除个人路径和标识信息。

## 限制与隐私

- 插件只影响这台 Mac 上的显示，不会改变其他会话参与者或设备看到的内容。
- 你自己撤回的消息，以及从其他设备撤回的消息，也可能继续在本机显示。
- 插件无法恢复已经删除的消息，也无法恢复从未下载或已经不可用的媒体文件。
- 某些消息类型或微信功能的行为可能发生变化，不保证兼容未来版本。
- 插件不会读取或导出聊天数据库、账号凭据或消息内容。日志仅包含启用情况和兼容性状态，不包含聊天内容。
- WeChatTool 是独立项目。生成的应用使用本机签名，不使用微信原发布者的签名身份。

## 许可证

MIT。适用的许可条款请参阅 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
