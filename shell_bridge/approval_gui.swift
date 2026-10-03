import Cocoa
import Foundation

struct ApprovalRequest: Codable, Equatable {
    let `protocol`: Int
    let kind: String
    let request_id: String
    let nonce: String
    let payload_sha256: String
    let category: String
    let cwd: String
    let command: String
    let explanation: String
    let expires_at: Double
}

struct ApprovalDecision: Codable {
    let `protocol`: Int
    let kind: String
    let request_id: String
    let nonce: String
    let payload_sha256: String
    let decision: String
    let decided_at: Double
}

final class ApprovalStore {
    let pending: URL
    let decisions: URL
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    init(root: URL) throws {
        pending = root.appendingPathComponent("pending", isDirectory: true)
        decisions = root.appendingPathComponent("decisions", isDirectory: true)
        try FileManager.default.createDirectory(at: pending, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: decisions, withIntermediateDirectories: true)
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    }

    func pendingRequests(now: Double = Date().timeIntervalSince1970) -> [ApprovalRequest] {
        guard let urls = try? FileManager.default.contentsOfDirectory(at: pending, includingPropertiesForKeys: nil, options: [.skipsHiddenFiles]) else { return [] }
        return urls.filter { $0.pathExtension == "json" }.compactMap { url in
            guard let data = try? Data(contentsOf: url),
                  let req = try? decoder.decode(ApprovalRequest.self, from: data),
                  req.protocol == 1, req.kind == "operator_approval_request",
                  !req.request_id.isEmpty, !req.nonce.isEmpty,
                  req.payload_sha256.count == 64, req.expires_at > now else { return nil }
            let decisionURL = decisions.appendingPathComponent(req.request_id + ".json")
            return FileManager.default.fileExists(atPath: decisionURL.path) ? nil : req
        }.sorted { $0.expires_at < $1.expires_at }
    }

    func decide(_ req: ApprovalRequest, decision: String) throws {
        guard decision == "allow" || decision == "cancel" else { throw NSError(domain: "ApprovalGUI", code: 10) }
        let pendingURL = pending.appendingPathComponent(req.request_id + ".json")
        let current = try decoder.decode(ApprovalRequest.self, from: Data(contentsOf: pendingURL))
        guard current == req else { throw NSError(domain: "ApprovalGUI", code: 11, userInfo: [NSLocalizedDescriptionKey: "Approval request changed; reload it before deciding."]) }
        guard current.expires_at > Date().timeIntervalSince1970 else { throw NSError(domain: "ApprovalGUI", code: 12, userInfo: [NSLocalizedDescriptionKey: "Approval request expired."]) }
        let out = ApprovalDecision(protocol: 1, kind: "operator_approval_decision", request_id: current.request_id, nonce: current.nonce, payload_sha256: current.payload_sha256, decision: decision, decided_at: Date().timeIntervalSince1970)
        let target = decisions.appendingPathComponent(current.request_id + ".json")
        let temporary = decisions.appendingPathComponent("." + current.request_id + "." + UUID().uuidString + ".tmp")
        let encoded = try encoder.encode(out)
        try encoded.write(to: temporary)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
        do {
            try FileManager.default.moveItem(at: temporary, to: target)
        } catch {
            try? FileManager.default.removeItem(at: temporary)
            throw error
        }
    }
}

final class ApprovalWindowController: NSWindowController {
    private let store: ApprovalStore
    private let request: ApprovalRequest
    private let completion: () -> Void

    init(store: ApprovalStore, request: ApprovalRequest, completion: @escaping () -> Void) {
        self.store = store
        self.request = request
        self.completion = completion
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 680, height: 520), styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Local Executor Bridge Approval"
        window.center()
        window.isReleasedWhenClosed = false
        super.init(window: window)
        buildUI()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    private func label(_ text: String, bold: Bool = false) -> NSTextField {
        let field = NSTextField(labelWithString: text)
        field.lineBreakMode = .byWordWrapping
        field.maximumNumberOfLines = 0
        field.font = bold ? .boldSystemFont(ofSize: NSFont.systemFontSize) : .systemFont(ofSize: NSFont.systemFontSize)
        return field
    }

    private func buildUI() {
        guard let content = window?.contentView else { return }
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)
        stack.addArrangedSubview(label("Approval required", bold: true))
        stack.addArrangedSubview(label(request.explanation))
        stack.addArrangedSubview(label("Category: \(request.category)"))
        stack.addArrangedSubview(label("Working directory: \(request.cwd)"))
        stack.addArrangedSubview(label("Request: \(request.request_id)"))
        stack.addArrangedSubview(label("Fingerprint: \(String(request.payload_sha256.prefix(20)))…"))
        stack.addArrangedSubview(label("Exact command", bold: true))

        let textView = NSTextView()
        textView.isEditable = false
        textView.isSelectable = true
        textView.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        textView.string = request.command
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.documentView = textView
        scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 180).isActive = true
        stack.addArrangedSubview(scroll)
        scroll.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true

        let buttons = NSStackView()
        buttons.orientation = .horizontal
        buttons.spacing = 12
        let reject = NSButton(title: "Reject", target: self, action: #selector(rejectRequest))
        let approve = NSButton(title: "Approve once", target: self, action: #selector(approveRequest))
        approve.keyEquivalent = "\r"
        buttons.addArrangedSubview(reject)
        buttons.addArrangedSubview(approve)
        stack.addArrangedSubview(buttons)

        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 20),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: content.bottomAnchor, constant: -20)
        ])
    }

    private func finish(_ decision: String) {
        do {
            try store.decide(request, decision: decision)
            close()
            completion()
        } catch {
            NSAlert(error: error).runModal()
        }
    }

    @objc private func approveRequest() { finish("allow") }
    @objc private func rejectRequest() { finish("cancel") }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var statusItem: NSStatusItem!
    private var store: ApprovalStore!
    private var timer: Timer?
    private var windows: [String: ApprovalWindowController] = [:]
    private var seen = Set<String>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        guard let root = Bundle.main.object(forInfoDictionaryKey: "ApprovalRoot") as? String, !root.isEmpty else { fatalError("ApprovalRoot missing from Info.plist") }
        store = try! ApprovalStore(root: URL(fileURLWithPath: root, isDirectory: true))
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "Bridge"
        statusItem.button?.toolTip = "Local Executor Bridge approvals"
        refresh(showNew: true)
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in self?.refresh(showNew: true) }
    }

    private func refresh(showNew: Bool) {
        let requests = store.pendingRequests()
        let ids = Set(requests.map { $0.request_id })
        let newIDs = ids.subtracting(seen)
        seen = ids
        statusItem.button?.title = requests.isEmpty ? "Bridge" : "Bridge \(requests.count)"
        let menu = NSMenu()
        if requests.isEmpty {
            let none = NSMenuItem(title: "No pending approvals", action: nil, keyEquivalent: "")
            none.isEnabled = false
            menu.addItem(none)
        } else {
            let heading = NSMenuItem(title: "\(requests.count) pending approval\(requests.count == 1 ? "" : "s")", action: nil, keyEquivalent: "")
            heading.isEnabled = false
            menu.addItem(heading)
            menu.addItem(.separator())
            for req in requests {
                let title = req.explanation.count > 64 ? String(req.explanation.prefix(61)) + "…" : req.explanation
                let item = NSMenuItem(title: title, action: #selector(openFromMenu(_:)), keyEquivalent: "")
                item.target = self
                item.representedObject = req.request_id
                menu.addItem(item)
            }
        }
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Approval UI", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu
        if showNew, let id = newIDs.sorted().first, let req = requests.first(where: { $0.request_id == id }) { show(req) }
    }

    @objc private func openFromMenu(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String, let req = store.pendingRequests().first(where: { $0.request_id == id }) else { return }
        show(req)
    }

    private func show(_ req: ApprovalRequest) {
        let controller: ApprovalWindowController
        if let existing = windows[req.request_id] {
            controller = existing
        } else {
            controller = ApprovalWindowController(store: store, request: req) { [weak self] in
                self?.windows.removeValue(forKey: req.request_id)
                self?.refresh(showNew: false)
            }
            controller.window?.delegate = self
            windows[req.request_id] = controller
        }
        controller.showWindow(nil)
        controller.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow, let pair = windows.first(where: { $0.value.window === window }) else { return }
        windows.removeValue(forKey: pair.key)
    }

    @objc private func quitApp() { NSApp.terminate(nil) }
}

func selfTest() -> Int32 {
    do {
        let base = ProcessInfo.processInfo.environment["APPROVAL_SELF_TEST_ROOT"] ?? FileManager.default.currentDirectoryPath
        let root = URL(fileURLWithPath: base, isDirectory: true).appendingPathComponent("approval-selftest-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try ApprovalStore(root: root)
        let req = ApprovalRequest(protocol: 1, kind: "operator_approval_request", request_id: "self-test", nonce: "abc", payload_sha256: String(repeating: "a", count: 64), category: "self_test", cwd: "/tmp", command: "true", explanation: "Self-test", expires_at: Date().timeIntervalSince1970 + 60)
        try JSONEncoder().encode(req).write(to: store.pending.appendingPathComponent("self-test.json"))
        guard store.pendingRequests().count == 1 else { return 20 }
        try store.decide(req, decision: "allow")
        let out = try JSONDecoder().decode(ApprovalDecision.self, from: Data(contentsOf: store.decisions.appendingPathComponent("self-test.json")))
        return out.request_id == req.request_id && out.nonce == req.nonce && out.payload_sha256 == req.payload_sha256 && out.decision == "allow" ? 0 : 21
    } catch {
        fputs("self-test failed: \(error)\n", stderr)
        return 22
    }
}

if CommandLine.arguments.contains("--self-test") { exit(selfTest()) }
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
