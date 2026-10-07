import AppKit
import Foundation

struct ApprovalRequest: Codable {
    let `protocol`: Int
    let kind: String
    let request_id: String
    let category: String
    let explanation: String
    let cwd: String
    let expires_at: Double
    let created_at: Double?
}

final class ApprovalController: NSObject, NSApplicationDelegate {
    private let approvalRoot: URL
    private let helperPath: String
    private let pythonPath: String
    private var statusItem: NSStatusItem!
    private var lastDecisionError: String?
    private let menu = NSMenu(title: "Approvals")
    private var timer: Timer?

    override init() {
        let info = Bundle.main.infoDictionary ?? [:]
        let root = info["ApprovalRoot"] as? String ?? ""
        helperPath = info["ApprovalHelperPath"] as? String ?? ""
        pythonPath = info["ApprovalPythonPath"] as? String ?? ""
        approvalRoot = URL(fileURLWithPath: root, isDirectory: true)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "🌉"
        statusItem.button?.toolTip = "Local Executor Bridge approvals"
        statusItem.menu = menu
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    private func pendingRequests() -> [ApprovalRequest] {
        let pending = approvalRoot.appendingPathComponent("pending", isDirectory: true)
        let fm = FileManager.default
        guard let urls = try? fm.contentsOfDirectory(at: pending, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey], options: [.skipsHiddenFiles]) else { return [] }
        let decoder = JSONDecoder()
        let now = Date().timeIntervalSince1970
        return urls.compactMap { url in
            guard url.pathExtension == "json",
                  let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]),
                  values.isRegularFile == true, values.isSymbolicLink != true,
                  let data = try? Data(contentsOf: url),
                  let req = try? decoder.decode(ApprovalRequest.self, from: data),
                  req.protocol == 1, req.kind == "operator_approval_request", req.expires_at > now,
                  url.lastPathComponent == req.request_id + ".json"
            else { return nil }
            let decision = approvalRoot.appendingPathComponent("decisions/" + req.request_id + ".json")
            return fm.fileExists(atPath: decision.path) ? nil : req
        }.sorted { ($0.created_at ?? 0) < ($1.created_at ?? 0) }
    }

    private func short(_ text: String, _ limit: Int) -> String {
        let compact = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard compact.count > limit else { return compact }
        return String(compact.prefix(max(0, limit - 1))) + "…"
    }

    @objc private func allow(_ sender: NSMenuItem) { decide(sender, "allow") }
    @objc private func reject(_ sender: NSMenuItem) { decide(sender, "cancel") }

    private func decide(_ sender: NSMenuItem, _ decision: String) {
        guard let requestID = sender.representedObject as? String,
              let req = pendingRequests().first(where: { $0.request_id == requestID }) else {
            refresh(); return
        }
        let pending = approvalRoot.appendingPathComponent("pending/" + req.request_id + ".json").path
        let out = approvalRoot.appendingPathComponent("decisions/" + req.request_id + ".json").path
        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.arguments = [helperPath, "--decide", pending, out, decision]
        do {
            guard !pythonPath.isEmpty else {
                lastDecisionError = "Approval helper Python path is missing"
                refresh()
                return
            }
            try process.run()
            process.waitUntilExit()
            if process.terminationStatus == 0 {
                lastDecisionError = nil
            } else {
                lastDecisionError = "Approval decision failed (exit \(process.terminationStatus))"
            }
        } catch {
            lastDecisionError = "Approval decision failed: \(error.localizedDescription)"
        }
        refresh()
    }

    @objc private func showAbout(_ sender: NSMenuItem) {
        let alert = NSAlert()
        alert.messageText = "Local Executor Bridge"
        alert.informativeText = "Approval queue: menu-only Allow once / Reject; no identity authentication."
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    @objc private func quit(_ sender: NSMenuItem) { NSApp.terminate(nil) }

    private func disabledItem(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    private func refresh() {
        let records = pendingRequests()
        menu.removeAllItems()
        menu.addItem(disabledItem("Local Executor Bridge v6"))
        if let lastDecisionError {
            menu.addItem(disabledItem("Error: " + short(lastDecisionError, 100)))
        }
        menu.addItem(.separator())
        if records.isEmpty {
            menu.addItem(disabledItem("No approvals pending"))
        } else {
            for req in records {
                let item = NSMenuItem(title: short(req.explanation.isEmpty ? req.request_id : req.explanation, 72), action: nil, keyEquivalent: "")
                let submenu = NSMenu(title: "Approval")
                submenu.addItem(disabledItem("What: " + short(req.explanation.isEmpty ? req.request_id : req.explanation, 110)))
                submenu.addItem(disabledItem("Why: " + short(req.category.replacingOccurrences(of: "_", with: " "), 110)))
                submenu.addItem(disabledItem("Where: " + short(req.cwd, 110)))
                submenu.addItem(.separator())
                let allowItem = NSMenuItem(title: "Allow once", action: #selector(allow(_:)), keyEquivalent: "")
                allowItem.target = self
                allowItem.representedObject = req.request_id
                submenu.addItem(allowItem)
                let rejectItem = NSMenuItem(title: "Reject", action: #selector(reject(_:)), keyEquivalent: "")
                rejectItem.target = self
                rejectItem.representedObject = req.request_id
                submenu.addItem(rejectItem)
                item.submenu = submenu
                menu.addItem(item)
            }
        }
        statusItem.button?.title = records.isEmpty ? "🌉" : "🌉 \(records.count)"
        menu.addItem(.separator())
        let about = NSMenuItem(title: "About Local Executor Bridge", action: #selector(showAbout(_:)), keyEquivalent: "")
        about.target = self
        menu.addItem(about)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit approval queue", action: #selector(quit(_:)), keyEquivalent: "")
        quit.target = self
        menu.addItem(quit)
    }
}

let app = NSApplication.shared
let delegate = ApprovalController()
app.delegate = delegate
app.run()

