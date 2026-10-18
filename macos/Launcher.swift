import AppKit
import Foundation
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {
    var window: NSWindow!
    var engine: Process?
    var input = Pipe()
    var output = Pipe()
    var statusLabel: NSTextField!
    var webView: WKWebView!
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
        let editItem = NSMenuItem(); menu.addItem(editItem)
        let editMenu = NSMenu(title: "编辑"); editItem.submenu = editMenu
        for (title, action, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        let viewItem = NSMenuItem(); menu.addItem(viewItem)
        let viewMenu = NSMenu(title: "显示"); viewItem.submenu = viewMenu
        let reload = viewMenu.addItem(withTitle: "重新载入工作台", action: #selector(openWorkspace), keyEquivalent: "r"); reload.target = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1220, height: 820), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "拾光 · LightHarbor"
        window.minSize = NSSize(width: 820, height: 600)
        window.setFrameAutosaveName("LightHarborWorkbench")
        window.isReleasedWhenClosed = false
        window.delegate = self
        let body = NSView(); window.contentView = body
        statusLabel = NSTextField(labelWithString: "正在启动本地工作台…")
        statusLabel.font = .systemFont(ofSize: 12)
        statusLabel.textColor = .secondaryLabelColor
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "lightHarbor")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self; webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.translatesAutoresizingMaskIntoConstraints = false
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        body.addSubview(webView); body.addSubview(statusLabel)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: body.leadingAnchor), webView.trailingAnchor.constraint(equalTo: body.trailingAnchor),
            webView.topAnchor.constraint(equalTo: body.topAnchor), webView.bottomAnchor.constraint(equalTo: statusLabel.topAnchor, constant: -6),
            statusLabel.leadingAnchor.constraint(equalTo: body.leadingAnchor, constant: 16), statusLabel.trailingAnchor.constraint(equalTo: body.trailingAnchor, constant: -16),
            statusLabel.bottomAnchor.constraint(equalTo: body.bottomAnchor, constant: -7), statusLabel.heightAnchor.constraint(equalToConstant: 18)
        ])
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
                pageURL = url; phase = "idle"
                statusLabel.stringValue = "已就绪 · 所有操作在你的电脑完成"
                if !quitting { openWorkspace() }
            case "status":
                phase = message["phase"] as? String ?? phase
                if !quitting {
                    let descriptions = ["idle": "已就绪", "scanning": "正在扫描照片", "ready": "预览已生成，可以调整", "preparing": "正在检查整理计划", "reviewing": "等待最终确认，尚未复制", "copying": "正在复制并校验", "paused": "复制已暂停，可继续", "done": "本次整理已结束", "cancelled": "已停止，原件保留", "error": "请在工作台查看提示"]
                    statusLabel.stringValue = descriptions[phase] ?? "本地服务运行中"
                }
            default: break
            }
        }
    }

    @objc func openWorkspace() {
        if !quitting, let url = pageURL { webView.load(URLRequest(url: url)) }
    }
    func isLocal(_ url: URL?) -> Bool {
        guard let url = url, let base = pageURL else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == base.port
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(isLocal(navigationAction.request.url) ? .allow : .cancel)
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if !quitting { fail("工作台载入失败，可按 ⌘R 重试：" + error.localizedDescription) }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if !quitting { fail("界面已暂停，按 ⌘R 恢复。当前整理任务仍由本地服务处理。") }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard frame.isMainFrame, isLocal(frame.request.url), !quitting else { completionHandler(false); return }
        let alert = NSAlert(); alert.messageText = "请确认"; alert.informativeText = message
        alert.addButton(withTitle: "继续"); alert.addButton(withTitle: "取消")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard !quitting, message.frameInfo.isMainFrame, isLocal(message.frameInfo.request.url), let body = message.body as? [String: Any] else { replyHandler(nil, "请求不可用"); return }
        switch body["action"] as? String {
        case "choose":
            let panel = NSOpenPanel(); panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
            panel.canCreateDirectories = body["target"] as? String == "destination"
            panel.message = panel.canCreateDirectories ? "选择整理后的保存文件夹" : "选择存储卡或照片文件夹"
            panel.prompt = "选择文件夹"
            panel.beginSheetModal(for: window) { response in replyHandler(["path": response == .OK ? (panel.url?.path ?? "") : ""], nil) }
        case "reveal":
            guard let path = body["path"] as? String, path.hasPrefix("/"), !path.contains("\u{0}"), FileManager.default.fileExists(atPath: path) else { replyHandler(nil, "文件夹不存在或磁盘未连接"); return }
            let opened = NSWorkspace.shared.open(URL(fileURLWithPath: path, isDirectory: true))
            replyHandler(opened ? ["ok": true] : nil, opened ? nil : "无法打开文件夹")
        case "copy":
            guard let text = body["text"] as? String, text.utf8.count < 32768 else { replyHandler(nil, "内容过长"); return }
            NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
            replyHandler(["ok": true], nil)
        default: replyHandler(nil, "不支持的操作")
        }
    }
    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "拾光 · LightHarbor", .applicationVersion: "1.3.13", .credits: NSAttributedString(string: "本地照片与延时摄影整理工具\nhttps://github.com/yunqingzhou9-spec/lightharbor")])
    }
    func fail(_ message: String) { statusLabel.stringValue = message }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { window.makeKeyAndOrderFront(nil); return true }
    func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminated || engine?.isRunning != true { return .terminateNow }
        if quitting { return .terminateCancel }
        let alert = NSAlert(); alert.messageText = "退出拾光？"
        alert.informativeText = ["scanning", "preparing", "copying", "paused"].contains(phase)
            ? "当前任务会安全停止。已完成的副本保留，未完成的复制会清理；原件不受影响。已生成的整理计划会保存在本机，下次恢复后核对并确认即可继续。扫描中退出可在下次重新扫描。"
            : "整理计划和复制范围会保存在本机。下次可恢复任务，重新核对并确认后继续。已经整理的文件和原件不受影响。"
        alert.addButton(withTitle: "继续使用"); alert.addButton(withTitle: "退出")
        guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
        quitting = true; webView.isHidden = true
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
