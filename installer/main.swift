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
    private var destinationBundleID: String?
    private var compatible = false
    private var busy = false
    private var contentStack: NSStackView!

    private let sourceField = NSTextField(labelWithString: "")
    private let statusTitle = NSTextField(labelWithString: "")
    private let statusMessage = NSTextField(wrappingLabelWithString: "")
    private let details = NSTextView()
    private let detailsScroll = NSScrollView()
    private let spinner = NSProgressIndicator()
    private let chooseButton = NSButton()
    private let checkButton = NSButton()
    private let createButton = NSButton()
    private let openButton = NSButton()
    private let revealButton = NSButton()
    private let recallCheckbox = NSButton(checkboxWithTitle: Copy.text("Recall protection", "防撤回"), target: nil, action: nil)
    private let accessibilityCheckbox = NSButton(checkboxWithTitle: Copy.text("Accessibility support (experimental)", "辅助功能支持（实验性）"), target: nil, action: nil)

    private var selectedFeatures: [String] {
        var features: [String] = []
        if recallCheckbox.state == .on { features.append("recall") }
        if accessibilityCheckbox.state == .on { features.append("accessibility") }
        return features
    }

    private func featureSummary(_ features: [String]) -> String {
        features.map { feature in
            feature == "recall" ? Copy.text("recall protection", "防撤回")
                : Copy.text("experimental accessibility support", "实验性辅助功能支持")
        }.joined(separator: Copy.text(" and ", "和"))
    }

    private func verificationAdvice(_ features: [String]) -> String {
        var advice: [String] = []
        if features.contains("recall") {
            advice.append(Copy.text("After signing in, recall a new test message to verify it stays visible.", "登录后，请撤回一条新测试消息，确认它仍然可见。"))
        }
        if features.contains("accessibility") {
            advice.append(Copy.text("Test your accessibility tool after launch. This does not prevent account-security logouts.", "启动后请测试辅助功能工具。此功能不会阻止账号安全退出登录。"))
        }
        return advice.joined(separator: " ")
    }

    private static func matchesFeatures(_ report: [String: Any], _ requested: [String]) -> Bool {
        guard let reported = report["features"] as? [String] else { return false }
        return reported.count == requested.count && Set(reported) == Set(requested)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeMenu()
        makeWindow()
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
        appMenu.addItem(withTitle: Copy.text("About WeChatTool-Installer", "关于 WeChatTool 安装器"),
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: Copy.text("Quit WeChatTool-Installer", "退出 WeChatTool 安装器"),
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
        window.title = Copy.text("WeChatTool-Installer", "WeChatTool 安装器")
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        guard let content = window.contentView else { return }

        let title = NSTextField(labelWithString: Copy.text("Set up WeChatTool", "安装 WeChatTool"))
        title.font = .systemFont(ofSize: 26, weight: .semibold)
        let subtitle = NSTextField(wrappingLabelWithString: Copy.text(
            "Choose the features for your separate copy of WeChat.",
            "选择要为独立微信副本安装的功能。"))
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
            "Each copy has its own login, chats, and settings. Create a separate copy for each account and use them together. Your original app stays unchanged; its chat history is not copied.",
            "每个副本拥有独立的登录状态、聊天记录和设置。为每个账号创建一个副本，即可同时使用。原应用保持不变，原有聊天记录不会复制到新副本。"))
        explanation.font = .systemFont(ofSize: 12)
        explanation.textColor = .secondaryLabelColor

        let featureLabel = NSTextField(labelWithString: Copy.text("2. Choose one or both features", "2. 选择一项或两项功能"))
        featureLabel.font = .systemFont(ofSize: 14, weight: .semibold)
        recallCheckbox.state = .on
        accessibilityCheckbox.state = .off
        for checkbox in [recallCheckbox, accessibilityCheckbox] {
            checkbox.target = self
            checkbox.action = #selector(featuresChanged)
        }
        recallCheckbox.toolTip = Copy.text("Keep recalled messages visible in this copy.", "让撤回的消息在此副本中保持可见。")
        let accessibilityExplanation = NSTextField(wrappingLabelWithString: Copy.text(
            "Accessibility support keeps local UI automation available. It is experimental and does not prevent account-security logouts or replace macOS accessibility permission.",
            "辅助功能支持用于保持本地界面自动化可用。此功能为实验性功能，不会阻止账号安全退出登录，也不能代替 macOS 辅助功能授权。"))
        accessibilityExplanation.font = .systemFont(ofSize: 12)
        accessibilityExplanation.textColor = .secondaryLabelColor

        let separator = NSBox()
        separator.boxType = .separator
        let checkLabel = NSTextField(labelWithString: Copy.text("3. Check compatibility and create your copy", "3. 检查兼容性并创建副本"))
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

        configure(revealButton, Copy.text("Show in Finder", "在访达中显示"), #selector(revealCopy))
        configure(openButton, Copy.text("Open WeChat", "打开微信"), #selector(openCopy))
        let finishedActions = NSStackView(views: [revealButton, openButton])
        finishedActions.orientation = .horizontal
        finishedActions.spacing = 8

        let stack = NSStackView(views: [title, subtitle, sourceLabel, sourceRow, explanation,
                                      featureLabel, recallCheckbox, accessibilityCheckbox, accessibilityExplanation,
                                      separator, checkLabel, statusRow, statusMessage,
                                      detailsScroll, actions, finishedActions])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.setCustomSpacing(20, after: subtitle)
        stack.setCustomSpacing(18, after: explanation)
        stack.setCustomSpacing(6, after: recallCheckbox)
        stack.setCustomSpacing(6, after: accessibilityCheckbox)
        stack.setCustomSpacing(18, after: separator)
        stack.translatesAutoresizingMaskIntoConstraints = false
        contentStack = stack
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 28),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -28),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 26)
        ])
        for view in [subtitle, sourceRow, explanation, accessibilityExplanation, separator, statusMessage, detailsScroll] {
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

    private func updateControls() {
        chooseButton.isEnabled = !busy
        recallCheckbox.isEnabled = !busy
        accessibilityCheckbox.isEnabled = !busy
        checkButton.isEnabled = !busy && source != nil && !selectedFeatures.isEmpty
        createButton.isEnabled = !busy && compatible && !selectedFeatures.isEmpty
        openButton.isHidden = destination == nil || destinationBundleID == nil
        revealButton.isHidden = destination == nil
        openButton.isEnabled = !busy && destination != nil && destinationBundleID != nil
        revealButton.isEnabled = !busy
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
        destinationBundleID = nil
        compatible = false
        checkCompatibility()
    }

    @objc private func featuresChanged() {
        guard !busy else { return }
        compatible = false
        destination = nil
        destinationBundleID = nil
        if selectedFeatures.isEmpty {
            setStatus(Copy.text("Select at least one feature", "请至少选择一项功能"),
                      Copy.text("Choose recall protection, accessibility support, or both before creating a copy.", "创建副本前，请选择防撤回、辅助功能支持，或同时选择两项。"))
            updateControls()
        } else if source != nil {
            checkCompatibility()
        } else {
            setStatus(Copy.text("Choose your WeChat app", "请选择微信应用"),
                      Copy.text("Select the official WeChat application to check the selected features.", "请选择官方微信应用，以检查所选功能的兼容性。"))
            updateControls()
        }
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
        let features = selectedFeatures
        guard !features.isEmpty else {
            featuresChanged()
            return
        }
        compatible = false
        busy = true
        setStatus(Copy.text("Checking compatibility…", "正在检查兼容性…"),
                  Copy.text("Checking \(featureSummary(features)). This does not change WeChat or your chats.", "正在检查\(featureSummary(features))。此操作不会更改微信或聊天数据。"))
        updateControls()
        Backend.run(["analyze", "--app", source.path, "--json", "--features"] + features) { [weak self] result in
            guard let self else { return }
            self.busy = false
            switch result {
            case .success(let response):
                if response.exitCode == 0, let report = response.json,
                   report["status"] as? String == "structurally-compatible",
                   Self.matchesFeatures(report, features),
                   let version = report["version"] as? String, let build = report["build"] as? String {
                    self.compatible = true
                    self.setStatus(Copy.text("WeChat \(version) (\(build)) passed the check", "微信 \(version)（\(build)）已通过检查"),
                                   Copy.text("Ready to create a copy with \(self.featureSummary(features)). ", "可以创建包含\(self.featureSummary(features))的副本。") + self.verificationAdvice(features))
                } else {
                    self.setStatus(Copy.text("This WeChat app could not pass the check", "此微信应用未通过检查"),
                                   Copy.text("The selected features could not be verified. Choose a clean official installation or change your feature selection.", "无法验证所选功能的兼容性。请选择未经修改的官方微信，或更改所选功能。"), detail: response.details)
                }
            case .failure(let error):
                self.showBackendError(error)
            }
            self.updateControls()
        }
    }

    @objc private func createCopy() {
        guard !busy, compatible, !selectedFeatures.isEmpty, let source, window.attachedSheet == nil else { return }
        let panel = NSSavePanel()
        panel.title = Copy.text("Save your WeChat copy", "保存微信副本")
        panel.prompt = Copy.text("Create Copy", "创建副本")
        panel.message = Copy.text("Give this account’s copy a new name. Existing apps are never replaced.", "请为此账号的副本取一个新名称。现有应用不会被替换。")
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
            guard let self, !self.busy, response == .OK, let output = panel.url else { return }
            guard !FileManager.default.fileExists(atPath: output.path) else {
                self.alert(Copy.text("Choose a new name", "请选择新名称"),
                           Copy.text("An item already exists at that location. Choose a different name or folder; the installer will not replace it.", "该位置已有同名项目。请选择其他名称或文件夹，安装器不会替换现有项目。"))
                return
            }
            self.prepare(source: source, output: output)
        }
    }

    private func prepare(source: URL, output: URL) {
        let features = selectedFeatures
        guard compatible, !features.isEmpty else { return }
        busy = true
        destination = nil
        destinationBundleID = nil
        setStatus(Copy.text("Creating your WeChat copy…", "正在创建微信副本…"),
                  Copy.text("Installing \(featureSummary(features)) and checking the copy’s signature. This may take a few minutes.", "正在安装\(featureSummary(features))并检查副本签名，可能需要几分钟。"))
        updateControls()
        Backend.run(["prepare", "--app", source.path, "--output", output.path, "--plugin", Backend.plugin.path, "--features"] + features) { [weak self] result in
            guard let self else { return }
            self.busy = false
            switch result {
            case .success(let response):
                if response.exitCode == 0, let report = response.json,
                   report["status"] as? String == "prepared-and-signature-verified",
                   Self.matchesFeatures(report, features),
                   let path = report["destination"] as? String,
                   URL(fileURLWithPath: path).resolvingSymlinksInPath() == output.resolvingSymlinksInPath(),
                   let bundleID = Self.isolatedBundleID(in: report) {
                    self.destination = URL(fileURLWithPath: path)
                    self.destinationBundleID = bundleID
                    self.setStatus(Copy.text("Your WeChat copy is ready", "微信副本已准备好"),
                                   Copy.text("Installed \(self.featureSummary(features)). Open this copy and sign in separately. ", "已安装\(self.featureSummary(features))。打开此副本后请单独登录。") + self.verificationAdvice(features))
                    self.sourceField.toolTip = source.path
                    self.revealButton.toolTip = output.path
                    self.openButton.toolTip = output.path
                } else if response.exitCode == 0 {
                    self.setStatus(Copy.text("Could not verify the new installation", "无法验证新安装"),
                                   Copy.text("The installer could not confirm this copy’s selected features or separate data storage. Check the details below and create a fresh copy.", "安装器无法确认此副本的所选功能或独立数据存储。请查看以下详情，并重新创建副本。"), detail: response.details)
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
                  Copy.text("Make sure you opened the complete WeChatTool-Installer app. Try downloading a fresh copy for your Mac's processor.", "请确保打开的是完整的 WeChatTool 安装器应用。可重新下载适合此 Mac 处理器的版本。"),
                  detail: error.localizedDescription)
    }

    private static func isolatedBundleID(in report: [String: Any]) -> String? {
        guard report["data_isolation"] as? String == "per-installation",
              let instanceID = report["instance_id"] as? String,
              instanceID.utf8.count == 32,
              instanceID.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              let bundleID = report["bundle_id"] as? String,
              bundleID == "local.wechattool.wechat." + instanceID else { return nil }
        return bundleID
    }

    @objc private func revealCopy() {
        guard let destination else { return }
        NSWorkspace.shared.activateFileViewerSelecting([destination])
    }

    @objc private func openCopy() {
        guard !busy, let destination, let destinationBundleID else { return }
        guard Bundle(url: destination)?.bundleIdentifier == destinationBundleID else {
            alert(Copy.text("Could not verify the WeChat copy", "无法验证微信副本"),
                  Copy.text("The app at this location has changed. Create a fresh copy with the installer before opening it.", "此位置的应用已发生变化。请使用安装器重新创建副本后再打开。"))
            return
        }
        // Keep one process per installation, including while LaunchServices
        // finishes registering a newly created app.
        busy = true
        updateControls()
        if let running = NSWorkspace.shared.runningApplications.first(where: {
            $0.bundleIdentifier == destinationBundleID && !$0.isTerminated
        }) {
            let activated = running.activate(options: [.activateAllWindows])
            busy = false
            updateControls()
            if !activated {
                alert(Copy.text("This WeChat copy is already running", "此微信副本已在运行"),
                      Copy.text("Open its window from the Dock or Finder.", "请从程序坞或访达打开其窗口。"))
            }
            return
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.createsNewApplicationInstance = false
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
