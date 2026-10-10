import AppKit
import Foundation

struct ApprovalRequest: Codable {
    let `protocol`: Int
    let kind: String
    let request_id: String
    let category: String
    let explanation: String
    let cwd: String
    let command: String
    let payload_sha256: String
    let expires_at: Double
    let created_at: Double?
}

final class ApprovalController: NSObject, NSApplicationDelegate {
    private let approvalRoot: URL
    private let helperPath: String
    private let pythonPath: String
    private let instanceLabel: String
    private var statusItem: NSStatusItem!
    private var lastDecisionError: String?
    // A summary cannot authorize. Exact payload must be inspected in this app session.
    private var reviewed: [String: String] = [:]
    private let menu = NSMenu(title: "Approvals")
    private var timer: Timer?
    // Bring each new payload to the foreground exactly once; deferral remains fail-closed.
    private var foregrounded: [String: String] = [:]
    private var reviewOpen = false

    override init() {
        let info = Bundle.main.infoDictionary ?? [:]
        let root = info["ApprovalRoot"] as? String ?? ""
        helperPath = info["ApprovalHelperPath"] as? String ?? ""
        pythonPath = info["ApprovalPythonPath"] as? String ?? ""
        instanceLabel = info["ApprovalInstanceLabel"] as? String ?? "unidentified"
        approvalRoot = URL(fileURLWithPath: root, isDirectory: true)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let iconURL = Bundle.main.url(forResource: "bridge-menubar", withExtension: "png"),
           let icon = NSImage(contentsOf: iconURL) {
            icon.size = NSSize(width: 18, height: 18)
            icon.isTemplate = true
            statusItem.button?.image = icon
            statusItem.button?.imagePosition = .imageLeft
            statusItem.button?.title = ""
        } else {
            statusItem.button?.title = "Bridge"
        }
        statusItem.button?.toolTip = "Bridge approvals: " + instanceLabel
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

    private func riskDescription(_ category: String) -> String {
        switch category {
        case "filesystem_delete":
            return "A file may be deleted permanently, including untracked work that Git cannot restore."
        case "filesystem_recursive_delete":
            return "Files or entire directories may be removed. Untracked content may be unrecoverable."
        case "git_destructive_local":
            return "Git reset or clean may permanently discard uncommitted or untracked work."
        case "trusted_operation":
            return "A verified installed helper will run outside the normal shell sandbox. Check the action and permitted roots."
        case "non_repository_filesystem_mutation":
            return "Files outside the Git repository may be changed. The effects may not be undoable."
        default:
            return "This action has elevated or uncertain effects. Reject it if the exact changes are unclear."
        }
    }

    @objc private func review(_ sender: NSMenuItem) {
        guard let requestID = sender.representedObject as? String,
              let req = pendingRequests().first(where: { $0.request_id == requestID }) else {
            refresh(); return
        }
        foregrounded[req.request_id] = req.payload_sha256
        reviewRequest(requestID: req.request_id, payloadHash: req.payload_sha256)
    }

    private func reviewRequest(requestID: String, payloadHash: String) {
        guard !reviewOpen,
              let req = pendingRequests().first(where: {
                  $0.request_id == requestID && $0.payload_sha256 == payloadHash
              }) else { return }
        reviewOpen = true
        // A menu-bar-only accessory app may launch without making any approval visible.
        NSApp.setActivationPolicy(.regular)
        defer {
            reviewOpen = false
            NSApp.setActivationPolicy(.accessory)
            refresh()
        }
        let detail = """
        Bridge instance: \(instanceLabel)
        Request: \(req.request_id)
        Scope / reason: \(req.category)
        Directory: \(req.cwd)
        Payload SHA-256: \(req.payload_sha256)
        Expires: \(Date(timeIntervalSince1970: req.expires_at))

        Agent description (unverified):
        \(req.explanation)

        Exact command (read the entire command before allowing):
        \(req.command)
        """
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 650, height: 380))
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        let contents = NSTextView(frame: NSRect(x: 0, y: 0, width: 635, height: 380))
        contents.isEditable = false
        contents.isSelectable = true
        contents.isRichText = false
        contents.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        contents.string = detail
        contents.minSize = NSSize(width: 635, height: 380)
        contents.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        contents.isVerticallyResizable = true
        contents.textContainer?.widthTracksTextView = false
        contents.textContainer?.containerSize = NSSize(width: 3000, height: CGFloat.greatestFiniteMagnitude)
        scroll.documentView = contents
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Review high-impact action: " + instanceLabel
        alert.informativeText = riskDescription(req.category) + " Inspect the exact command below before choosing Allow once."
        alert.accessoryView = scroll
        // The safest option is the default. No approval is recorded without an explicit click.
        alert.addButton(withTitle: "Reject")
        alert.addButton(withTitle: "Review later")
        alert.addButton(withTitle: "Allow once")
        NSApp.activate(ignoringOtherApps: true)
        alert.window.makeKeyAndOrderFront(nil)
        let choice = alert.runModal()
        // A delayed/expired/replaced request cannot be authorized by an old review.
        guard pendingRequests().contains(where: {
            $0.request_id == requestID && $0.payload_sha256 == payloadHash
        }) else { return }
        if choice == .alertFirstButtonReturn {
            decideRequest(req, "cancel")
        } else if choice == .alertThirdButtonReturn {
            reviewed[req.request_id] = req.payload_sha256
            decideRequest(req, "allow")
        }
    }

    @objc private func allow(_ sender: NSMenuItem) { decide(sender, "allow") }
    @objc private func reject(_ sender: NSMenuItem) { decide(sender, "cancel") }

    private func decide(_ sender: NSMenuItem, _ decision: String) {
        guard let requestID = sender.representedObject as? String,
              let req = pendingRequests().first(where: { $0.request_id == requestID }) else {
            refresh(); return
        }
        decideRequest(req, decision)
    }

    private func decideRequest(_ req: ApprovalRequest, _ decision: String) {
        guard pendingRequests().contains(where: {
            $0.request_id == req.request_id && $0.payload_sha256 == req.payload_sha256
        }) else {
            lastDecisionError = "Approval request changed or expired"
            refresh(); return
        }
        guard decision != "allow" || reviewed[req.request_id] == req.payload_sha256 else {
            lastDecisionError = "Inspect the exact command before allowing"
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
        alert.informativeText = "New approvals open a foreground review; Allow once / Reject remain available in the menu. No identity authentication."
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
                let reviewItem = NSMenuItem(title: "Inspect exact command…", action: #selector(review(_:)), keyEquivalent: "")
                reviewItem.target = self
                reviewItem.representedObject = req.request_id
                submenu.addItem(reviewItem)
                let allowItem = NSMenuItem(title: "Allow once", action: #selector(allow(_:)), keyEquivalent: "")
                allowItem.isEnabled = reviewed[req.request_id] == req.payload_sha256
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
        statusItem.button?.title = records.isEmpty ? "" : " \(records.count)"
        if !reviewOpen, let req = records.first(where: {
            foregrounded[$0.request_id] != $0.payload_sha256
        }) {
            foregrounded[req.request_id] = req.payload_sha256
            DispatchQueue.main.async { [weak self] in
                self?.reviewRequest(requestID: req.request_id, payloadHash: req.payload_sha256)
            }
        }
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

