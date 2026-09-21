import AppKit
import Foundation

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var engine: Process?
    var input = Pipe()
    var output = Pipe()
    var statusLabel: NSTextField!
    var openButton: NSButton!
    var quitButton: NSButton!
    var pageURL: URL?
    var buffer = Data()
    var phase = "starting"
    var quitting = false
    var terminated = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem(); menu.addItem(item)
        let appMenu = NSMenu(); item.submenu = appMenu
        appMenu.addItem(withTitle: "关于拾光 LightHarbor", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "退出拾光", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 470, height: 330), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "拾光 · LightHarbor"
        window.isReleasedWhenClosed = false
        window.delegate = self
        let body = NSView(); window.contentView = body
        let title = NSTextField(labelWithString: "拾光  LightHarbor")
        title.font = .systemFont(ofSize: 28, weight: .semibold)
        let subtitle = NSTextField(wrappingLabelWithString: "让底片，回到各自的位置。\n照片与延时序列，先预览再整理。")
        subtitle.textColor = .secondaryLabelColor
        subtitle.font = .systemFont(ofSize: 14)
        statusLabel = NSTextField(wrappingLabelWithString: "正在启动本地整理服务…")
        statusLabel.font = .systemFont(ofSize: 13)
        openButton = NSButton(title: "打开整理工作台", target: self, action: #selector(openWorkspace))
        openButton.bezelStyle = .rounded; openButton.controlSize = .large; openButton.isEnabled = false
        openButton.keyEquivalent = "\r"
        quitButton = NSButton(title: "退出拾光", target: NSApp, action: #selector(NSApplication.terminate(_:)))
        quitButton.bezelStyle = .rounded
        let buttons = NSStackView(views: [openButton, quitButton]); buttons.spacing = 12
        let note = NSTextField(wrappingLabelWithString: "仅在本机处理，不上传照片。\n关闭浏览器不会退出；退出应用会清除未执行的预览计划。")
        note.font = .systemFont(ofSize: 11); note.textColor = .secondaryLabelColor
        let stack = NSStackView(views: [title, subtitle, statusLabel, buttons, note])
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false; body.addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: body.leadingAnchor, constant: 30), stack.trailingAnchor.constraint(equalTo: body.trailingAnchor, constant: -30), stack.topAnchor.constraint(equalTo: body.topAnchor, constant: 26)])
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        startEngine()
    }

    func startEngine() {
        guard let resource = Bundle.main.resourceURL else { fail("应用文件不完整，请重新下载。"); return }
        let process = Process()
        process.executableURL = resource.appendingPathComponent("engine/lightharbor-engine")
        // Do not inherit development Python/DYLD settings on another person's Mac.
        process.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": NSHomeDirectory(), "TMPDIR": NSTemporaryDirectory(), "LANG": "en_US.UTF-8", "PYTHONUTF8": "1"]
        process.standardInput = input; process.standardOutput = output; process.standardError = FileHandle.nullDevice
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            DispatchQueue.main.async { self?.receive(data) }
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.terminated = true; self.output.fileHandleForReading.readabilityHandler = nil
                if self.quitting { NSApp.reply(toApplicationShouldTerminate: true) }
                else { self.fail("本地服务已停止（代码 \(process.terminationStatus)）。请退出后重新打开拾光。"); self.pageURL = nil }
            }
        }
        engine = process
        do { try process.run() } catch { fail("无法启动整理服务：\(error.localizedDescription)"); terminated = true }
    }

    func receive(_ data: Data) {
        guard !data.isEmpty else { return }
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 10) {
            let line = buffer[..<newline]; buffer.removeSubrange(...newline)
            guard let message = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
            switch message["event"] as? String {
            case "ready":
                guard let raw = message["url"] as? String, let url = URL(string: raw), url.scheme == "http", url.host == "127.0.0.1", url.port != nil, url.fragment != nil else { fail("本地服务地址无效。"); return }
                pageURL = url; phase = "idle"; openButton.isEnabled = true
                statusLabel.stringValue = "已就绪 · 所有操作在你的电脑完成"
                if !quitting && ProcessInfo.processInfo.environment["LIGHTHARBOR_NO_AUTO_OPEN"] != "1" { openWorkspace() }
            case "status":
                phase = message["phase"] as? String ?? phase
                if !quitting {
                    let descriptions = ["idle": "已就绪", "scanning": "正在扫描照片", "ready": "预览已生成，可以调整", "preparing": "正在检查整理计划", "reviewing": "等待最终确认，尚未复制", "copying": "正在复制并校验", "done": "本次整理已结束", "cancelled": "已停止，原件保留", "error": "请在工作台查看提示"]
                    statusLabel.stringValue = descriptions[phase] ?? "本地服务运行中"
                }
            default: break
            }
        }
    }

    @objc func openWorkspace() {
        if let url = pageURL { NSWorkspace.shared.open(url) }
    }
    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "拾光 · LightHarbor", .applicationVersion: "1.0.0", .credits: NSAttributedString(string: "本地照片与延时摄影整理工具\nhttps://github.com/yunqingzhou9-spec/lightharbor")])
    }
    func fail(_ message: String) { statusLabel.stringValue = message; openButton.isEnabled = false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { window.makeKeyAndOrderFront(nil); return true }
    func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminated || engine?.isRunning != true { return .terminateNow }
        if quitting { return .terminateCancel }
        let alert = NSAlert(); alert.messageText = "退出拾光？"
        alert.informativeText = ["scanning", "preparing", "copying"].contains(phase)
            ? "当前任务会安全停止。已完成的副本保留，未完成的复制会清理；原件不受影响。未执行的预览调整不会保存。"
            : "退出后，未执行的预览计划和调整不会保存。已经整理的文件和原件不受影响。"
        alert.addButton(withTitle: "继续使用"); alert.addButton(withTitle: "退出")
        guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
        quitting = true; openButton.isEnabled = false; quitButton.isEnabled = false
        statusLabel.stringValue = "正在安全停止，请稍候…"
        do { try input.fileHandleForWriting.write(contentsOf: Data("shutdown\n".utf8)); try input.fileHandleForWriting.close() }
        catch { engine?.terminate() }
        return .terminateLater
    }
}
let application = NSApplication.shared
application.setActivationPolicy(.regular)
let delegate = AppDelegate()
application.delegate = delegate
application.run()
