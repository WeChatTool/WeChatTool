import AppKit
import UniformTypeIdentifiers

private enum Copy {
    static let chinese = Locale.preferredLanguages.first?.hasPrefix("zh") == true
    static func text(_ english: String, _ chinese: String) -> String {
        Self.chinese ? chinese : english
    }
}

private struct BackendResult {
    let exitCode: Int32
    let output: Data
    let errorOutput: Data

    var json: [String: Any]? {
        (try? JSONSerialization.jsonObject(with: output)) as? [String: Any]
    }

    var details: String {
        let error = String(decoding: errorOutput, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        if !error.isEmpty { return error }
        if let problems = json?["problems"] as? [String], !problems.isEmpty {
            return problems.joined(separator: "\n")
        }
        return String(decoding: output, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

private enum Backend {
    static var resourceDirectory: URL { Bundle.main.resourceURL ?? Bundle.main.bundleURL }
    static var executable: URL { resourceDirectory.appendingPathComponent("backend/wechattool-backend") }
    static var plugin: URL { resourceDirectory.appendingPathComponent("WeChatTool.dylib") }

    static func run(_ arguments: [String], completion: @escaping (Result<BackendResult, Error>) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            let output = Pipe()
            let errors = Pipe()
            process.executableURL = executable
            process.arguments = arguments
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = output
            process.standardError = errors
            do {
                try process.run()
                // Drain both streams independently so a verbose error cannot fill a pipe.
                let reader = DispatchGroup()
                let errorData = LockedData()
                reader.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    errorData.set(errors.fileHandleForReading.readDataToEndOfFile())
                    reader.leave()
                }
                let outputData = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                reader.wait()
                let result = BackendResult(exitCode: process.terminationStatus,
                                           output: outputData, errorOutput: errorData.get())
                DispatchQueue.main.async { completion(.success(result)) }
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }
}

private final class LockedData {
    private let lock = NSLock()
    private var value = Data()
    func set(_ data: Data) { lock.lock(); value = data; lock.unlock() }
    func get() -> Data { lock.lock(); defer { lock.unlock() }; return value }
}

private final class Installer: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var source: URL?
    private var destination: URL?
    private var compatible = false
    private var busy = false
    private var observers: [NSObjectProtocol] = []
    private var contentStack: NSStackView!

    private let sourceField = NSTextField(labelWithString: "")
    private let statusTitle = NSTextField(labelWithString: "")
    private let statusMessage = NSTextField(wrappingLabelWithString: "")
    private let runningNote = NSTextField(wrappingLabelWithString: "")
    private let details = NSTextView()
    private let detailsScroll = NSScrollView()
    private let spinner = NSProgressIndicator()
    private let chooseButton = NSButton()
    private let checkButton = NSButton()
    private let createButton = NSButton()
    private let openButton = NSButton()
    private let revealButton = NSButton()

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeMenu()
        makeWindow()
        let notifications = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.didLaunchApplicationNotification, NSWorkspace.didTerminateApplicationNotification] {
            observers.append(notifications.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.updateControls()
            })
        }
        let installed = URL(fileURLWithPath: "/Applications/WeChat.app", isDirectory: true)
        if FileManager.default.fileExists(atPath: installed.path) {
            selectSource(installed)
        } else {
            sourceField.stringValue = Copy.text("No WeChat app selected", "尚未选择微信应用")
            setStatus(Copy.text("Choose your WeChat app", "请选择微信应用"),
                      Copy.text("Select the official WeChat application installed on this Mac.", "选择此 Mac 上安装的官方微信应用。"))
            updateControls()
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        canClose() ? .terminateNow : .terminateCancel
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { canClose() }

    private func canClose() -> Bool {
        guard busy else { return true }
        alert(Copy.text("Please wait", "请稍候"),
              Copy.text("Wait for the current operation to finish before quitting the installer.", "请等待当前操作完成后再退出安装器。"))
        return false
    }

    private func makeMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: Copy.text("About WeChatTool Installer", "关于 WeChatTool 安装器"),
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: Copy.text("Quit WeChatTool Installer", "退出 WeChatTool 安装器"),
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        let editItem = NSMenuItem(title: Copy.text("Edit", "编辑"), action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: editItem.title)
        editMenu.addItem(withTitle: Copy.text("Copy", "复制"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: Copy.text("Select All", "全选"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        menu.addItem(editItem)
        NSApp.mainMenu = menu
    }

    private func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 700, height: 580),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = Copy.text("WeChatTool Installer", "WeChatTool 安装器")
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        guard let content = window.contentView else { return }

        let title = NSTextField(labelWithString: Copy.text("Set up WeChatTool", "安装 WeChatTool"))
        title.font = .systemFont(ofSize: 26, weight: .semibold)
        let subtitle = NSTextField(wrappingLabelWithString: Copy.text(
            "Create a separate copy of WeChat with message recall protection.",
            "创建带有防撤回功能的微信副本。"))
        subtitle.font = .systemFont(ofSize: 14)
        subtitle.textColor = .secondaryLabelColor

        let sourceLabel = NSTextField(labelWithString: Copy.text("1. Select the official WeChat app", "1. 选择官方微信应用"))
        sourceLabel.font = .systemFont(ofSize: 14, weight: .semibold)
        sourceField.lineBreakMode = .byTruncatingMiddle
        sourceField.isSelectable = true
        sourceField.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        configure(chooseButton, Copy.text("Browse…", "浏览…"), #selector(chooseSource))
        let sourceRow = NSStackView(views: [sourceField, chooseButton])
        sourceRow.orientation = .horizontal
        sourceRow.spacing = 12

        let explanation = NSTextField(wrappingLabelWithString: Copy.text(
            "Your original application stays unchanged. The copy may use the same chat storage. Back up important chats and quit WeChat before creating or opening the copy.",
            "原应用保持不变。副本可能使用相同的聊天数据。请备份重要聊天，并在创建或打开副本前退出微信。"))
        explanation.font = .systemFont(ofSize: 12)
        explanation.textColor = .secondaryLabelColor

        let separator = NSBox()
        separator.boxType = .separator
        let checkLabel = NSTextField(labelWithString: Copy.text("2. Check compatibility and create your copy", "2. 检查兼容性并创建副本"))
        checkLabel.font = .systemFont(ofSize: 14, weight: .semibold)
        statusTitle.font = .systemFont(ofSize: 14, weight: .medium)
        statusMessage.font = .systemFont(ofSize: 12)
        statusMessage.textColor = .secondaryLabelColor
        statusMessage.maximumNumberOfLines = 4
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        let statusRow = NSStackView(views: [spinner, statusTitle])
        statusRow.orientation = .horizontal
        statusRow.spacing = 8

        details.isEditable = false
        details.isSelectable = true
        details.isRichText = false
        details.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        details.textColor = .secondaryLabelColor
        details.backgroundColor = .textBackgroundColor
        details.textContainerInset = NSSize(width: 8, height: 8)
        details.autoresizingMask = [.width]
        details.isVerticallyResizable = true
        details.isHorizontallyResizable = false
        details.textContainer?.widthTracksTextView = true
        detailsScroll.documentView = details
        detailsScroll.hasVerticalScroller = true
        detailsScroll.borderType = .bezelBorder
        detailsScroll.isHidden = true
        detailsScroll.heightAnchor.constraint(equalToConstant: 100).isActive = true

        configure(checkButton, Copy.text("Check Compatibility", "检查兼容性"), #selector(checkCompatibility))
        configure(createButton, Copy.text("Create WeChat Copy…", "创建微信副本…"), #selector(createCopy))
        createButton.keyEquivalent = "\r"
        let actions = NSStackView(views: [checkButton, createButton])
        actions.orientation = .horizontal
        actions.spacing = 8

        runningNote.font = .systemFont(ofSize: 12)
        runningNote.textColor = .secondaryLabelColor
        configure(revealButton, Copy.text("Show in Finder", "在访达中显示"), #selector(revealCopy))
        configure(openButton, Copy.text("Open WeChat", "打开微信"), #selector(openCopy))
        let finishedActions = NSStackView(views: [revealButton, openButton])
        finishedActions.orientation = .horizontal
        finishedActions.spacing = 8

        let stack = NSStackView(views: [title, subtitle, sourceLabel, sourceRow, explanation,
                                      separator, checkLabel, statusRow, statusMessage,
                                      detailsScroll, actions, runningNote, finishedActions])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.setCustomSpacing(20, after: subtitle)
        stack.setCustomSpacing(18, after: explanation)
        stack.setCustomSpacing(18, after: separator)
        stack.translatesAutoresizingMaskIntoConstraints = false
        contentStack = stack
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 28),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -28),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 26)
        ])
        for view in [subtitle, sourceRow, explanation, separator, statusMessage, detailsScroll, runningNote] {
            view.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        }
    }

    private func configure(_ button: NSButton, _ title: String, _ action: Selector) {
        button.title = title
        button.bezelStyle = .rounded
        button.target = self
        button.action = action
    }

    private func setStatus(_ title: String, _ message: String, detail: String = "") {
        statusTitle.stringValue = title
        statusMessage.stringValue = message
        details.string = detail
        detailsScroll.isHidden = detail.isEmpty
    }

    private var weChatIsRunning: Bool {
        NSWorkspace.shared.runningApplications.contains { $0.bundleIdentifier == "com.tencent.xinWeChat" && !$0.isTerminated }
    }

    private func updateControls() {
        chooseButton.isEnabled = !busy
        checkButton.isEnabled = !busy && source != nil
        createButton.isEnabled = !busy && compatible && !weChatIsRunning
        openButton.isHidden = destination == nil
        revealButton.isHidden = destination == nil
        openButton.isEnabled = !busy && !weChatIsRunning
        revealButton.isEnabled = !busy
        runningNote.stringValue = weChatIsRunning
            ? Copy.text("WeChat is running. Quit it normally to enable creating or opening a copy.", "微信正在运行。请正常退出微信，之后即可创建或打开副本。")
            : ""
        runningNote.isHidden = runningNote.stringValue.isEmpty
        if busy { spinner.startAnimation(nil) } else { spinner.stopAnimation(nil) }
        // Expand for diagnostics or longer translations while keeping controls visible.
        window.contentView?.layoutSubtreeIfNeeded()
        let height = max(500, contentStack.fittingSize.height + 50)
        var frame = window.frame
        let nextHeight = window.frameRect(forContentRect: NSRect(x: 0, y: 0, width: 700, height: height)).height
        frame.origin.y += frame.height - nextHeight
        frame.size.height = nextHeight
        if let screen = window.screen, frame.minY < screen.visibleFrame.minY {
            frame.origin.y = screen.visibleFrame.minY
        }
        window.setFrame(frame, display: true)
    }

    private func selectSource(_ url: URL) {
        source = url.standardizedFileURL
        sourceField.stringValue = source!.path
        sourceField.toolTip = source!.path
        destination = nil
        compatible = false
        checkCompatibility()
    }

    @objc private func chooseSource() {
        guard !busy else { return }
        let panel = NSOpenPanel()
        panel.title = Copy.text("Choose the official WeChat app", "选择官方微信应用")
        panel.prompt = Copy.text("Choose WeChat", "选择微信")
        panel.allowedContentTypes = [.applicationBundle]
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.treatsFilePackagesAsDirectories = false
        panel.directoryURL = URL(fileURLWithPath: "/Applications", isDirectory: true)
        panel.beginSheetModal(for: window) { [weak self] response in
            if response == .OK, let url = panel.url { self?.selectSource(url) }
        }
    }

    @objc private func checkCompatibility() {
        guard !busy, let source else { return }
        compatible = false
        busy = true
        setStatus(Copy.text("Checking compatibility…", "正在检查兼容性…"),
                  Copy.text("Reading the application. This does not change WeChat or your chats.", "正在读取应用，此操作不会更改微信或聊天数据。"))
        updateControls()
        Backend.run(["analyze", "--app", source.path, "--json"]) { [weak self] result in
            guard let self else { return }
            self.busy = false
            switch result {
            case .success(let response):
                if response.exitCode == 0, let report = response.json,
                   report["status"] as? String == "structurally-compatible",
                   let version = report["version"] as? String, let build = report["build"] as? String {
                    self.compatible = true
                    self.setStatus(Copy.text("WeChat \(version) (\(build)) passed the check", "微信 \(version)（\(build)）已通过检查"),
                                   Copy.text("Choose where to save your copy. After opening it, verify the feature with a new test message.", "请选择副本的保存位置。打开副本后，请用一条新测试消息验证防撤回功能。"))
                } else {
                    self.setStatus(Copy.text("This WeChat app could not pass the check", "此微信应用未通过检查"),
                                   Copy.text("Choose a clean official installation. If this version is unsupported, continue using the original app.", "请选择未经修改的官方微信。如果此版本不受支持，请继续使用原应用。"), detail: response.details)
                }
            case .failure(let error):
                self.showBackendError(error)
            }
            self.updateControls()
        }
    }

    @objc private func createCopy() {
        guard !busy, compatible, let source, requireWeChatClosed() else { return }
        let panel = NSSavePanel()
        panel.title = Copy.text("Save your WeChat copy", "保存微信副本")
        panel.prompt = Copy.text("Create Copy", "创建副本")
        panel.message = Copy.text("Choose a new name. Existing apps are never replaced.", "请选择一个新名称。现有应用不会被替换。")
        panel.allowedContentTypes = [.applicationBundle]
        panel.canCreateDirectories = true
        panel.treatsFilePackagesAsDirectories = false
        panel.isExtensionHidden = false
        panel.nameFieldStringValue = "WeChatTool-WeChat.app"
        let applications = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications", isDirectory: true)
        do { try FileManager.default.createDirectory(at: applications, withIntermediateDirectories: true) }
        catch {
            alert(Copy.text("Could not create the Applications folder", "无法创建 Applications 文件夹"), error.localizedDescription)
            return
        }
        panel.directoryURL = applications
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self, response == .OK, let output = panel.url, self.requireWeChatClosed() else { return }
            guard !FileManager.default.fileExists(atPath: output.path) else {
                self.alert(Copy.text("Choose a new name", "请选择新名称"),
                           Copy.text("An item already exists at that location. Choose a different name or folder; the installer will not replace it.", "该位置已有同名项目。请选择其他名称或文件夹，安装器不会替换现有项目。"))
                return
            }
            self.prepare(source: source, output: output)
        }
    }

    private func prepare(source: URL, output: URL) {
        busy = true
        destination = nil
        setStatus(Copy.text("Creating your WeChat copy…", "正在创建微信副本…"),
                  Copy.text("Copying the application and checking its signature. This may take a few minutes. Keep WeChat closed until this finishes.", "正在复制应用并检查签名，可能需要几分钟。请在完成前保持微信关闭。"))
        updateControls()
        Backend.run(["prepare", "--app", source.path, "--output", output.path, "--plugin", Backend.plugin.path]) { [weak self] result in
            guard let self else { return }
            self.busy = false
            switch result {
            case .success(let response):
                if response.exitCode == 0, let report = response.json,
                   report["status"] as? String == "prepared-and-signature-verified",
                   let path = report["destination"] as? String,
                   URL(fileURLWithPath: path).resolvingSymlinksInPath() == output.resolvingSymlinksInPath() {
                    self.destination = URL(fileURLWithPath: path)
                    self.setStatus(Copy.text("Your WeChat copy is ready", "微信副本已准备好"),
                                   Copy.text("Open the copy and log in normally. Send and recall a new test message to confirm it stays visible. Use this copy when you want recall protection.", "打开副本并正常登录。发送一条新测试消息后撤回，确认它仍然可见。需要防撤回时，请使用此副本。"))
                    self.sourceField.toolTip = source.path
                    self.revealButton.toolTip = output.path
                    self.openButton.toolTip = output.path
                } else {
                    self.setStatus(Copy.text("Could not create the copy", "无法创建副本"),
                                   Copy.text("The original app was not changed. Check the details below, then try again with a new destination.", "原应用未被更改。请查看以下详情，然后选择新的保存位置重试。"), detail: response.details)
                }
            case .failure(let error):
                self.showBackendError(error)
            }
            self.updateControls()
        }
    }

    private func showBackendError(_ error: Error) {
        setStatus(Copy.text("The installer could not run its bundled tools", "安装器无法运行内置工具"),
                  Copy.text("Make sure you opened the complete WeChatTool Installer app. Try downloading a fresh copy for your Mac's processor.", "请确保打开的是完整的 WeChatTool 安装器应用。可重新下载适合此 Mac 处理器的版本。"),
                  detail: error.localizedDescription)
    }

    private func requireWeChatClosed() -> Bool {
        guard weChatIsRunning else { return true }
        alert(Copy.text("Quit WeChat first", "请先退出微信"),
              Copy.text("Quit WeChat normally from its menu, then try again. Do not run the original app and a prepared copy together.", "请通过微信菜单正常退出微信后重试。不要同时运行原应用和已准备的副本。"))
        updateControls()
        return false
    }

    @objc private func revealCopy() {
        guard let destination else { return }
        NSWorkspace.shared.activateFileViewerSelecting([destination])
    }

    @objc private func openCopy() {
        guard !busy, let destination, requireWeChatClosed() else { return }
        // LaunchServices registers the app asynchronously. Block a second
        // click immediately, before a running-application notification arrives.
        busy = true
        updateControls()
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.createsNewApplicationInstance = true
        NSWorkspace.shared.openApplication(at: destination, configuration: configuration) { [weak self] _, error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.busy = false
                self.updateControls()
                if let error {
                    self.alert(Copy.text("Could not open the WeChat copy", "无法打开微信副本"), error.localizedDescription)
                }
            }
        }
    }

    private func alert(_ title: String, _ message: String) {
        // Repeated keyboard shortcuts must not stack sheets while an operation is running.
        guard window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: Copy.text("OK", "好"))
        alert.beginSheetModal(for: window)
    }
}

let app = NSApplication.shared
private let installer = Installer()
app.setActivationPolicy(.regular)
app.delegate = installer
app.run()
