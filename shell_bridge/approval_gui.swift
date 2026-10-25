import Cocoa
import CryptoKit
import Darwin
import Foundation
import LocalAuthentication
import Security

struct ApprovalRequest: Codable, Equatable {
  let `protocol`: Int
  let schema: Int
  let kind: String
  let request_id: String
  let bridge_instance_id: String
  let nonce: String
  let payload_sha256: String
  let category: String
  let explanation: String
  let cwd: String
  let command: String
  let requested_write_scope: String
  let effective_write_scope: [String: JSONValue]
  let network_authority: Bool
  let authority_summary: [String: JSONValue]
  let effect_summary: [String: JSONValue]
  let expires_at: Double
}

enum JSONValue: Codable, Equatable {
  case string(String)
  case bool(Bool)
  case number(Double)
  case array([JSONValue])
  case object([String: JSONValue])
  case null
  init(from decoder: Decoder) throws {
    let c = try decoder.singleValueContainer()
    if c.decodeNil() {
      self = .null
    } else if let v = try? c.decode(Bool.self) {
      self = .bool(v)
    } else if let v = try? c.decode(Double.self) {
      self = .number(v)
    } else if let v = try? c.decode(String.self) {
      self = .string(v)
    } else if let v = try? c.decode([JSONValue].self) {
      self = .array(v)
    } else {
      self = .object(try c.decode([String: JSONValue].self))
    }
  }
  func encode(to encoder: Encoder) throws {
    var c = encoder.singleValueContainer()
    switch self {
    case .string(let v): try c.encode(v)
    case .bool(let v): try c.encode(v)
    case .number(let v): try c.encode(v)
    case .array(let v): try c.encode(v)
    case .object(let v): try c.encode(v)
    case .null: try c.encodeNil()
    }
  }
}

struct ApprovalDecision: Codable {
  let `protocol`: Int
  let schema: Int
  let kind: String
  let request_id: String
  let bridge_instance_id: String
  let nonce: String
  let payload_sha256: String
  let decision: String
  let decided_at: Double
  let client: String
  let signer_key_id: String
  let signature_algorithm: String
  let signature_b64: String
  let step_up: [String: JSONValue]
}

func validRequestID(_ value: String) -> Bool {
  guard !value.isEmpty, value.count <= 128 else { return false }
  let bytes = Array(value.utf8)
  guard let first = bytes.first, (48...57).contains(first) || (97...122).contains(first) else {
    return false
  }
  return bytes.allSatisfy {
    (48...57).contains($0) || (97...122).contains($0) || $0 == 46 || $0 == 95 || $0 == 45
  }
}
func validHex(_ value: String, count: Int) -> Bool {
  value.utf8.count == count
    && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
}
func sha256Hex(_ data: Data) -> String {
  SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

final class ApprovalSigner {
  private let tag = "net.laurenzo.local-executor-approval.signing.v1".data(using: .utf8)!
  let publicKeyURL: URL
  init(root: URL) { publicKeyURL = root.appendingPathComponent("approver-public-key.pem") }
  private func key(context: LAContext) throws -> SecKey {
    let query: [String: Any] = [
      kSecClass as String: kSecClassKey, kSecAttrApplicationTag as String: tag,
      kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom, kSecReturnRef as String: true,
      kSecUseAuthenticationContext as String: context,
    ]
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    if status == errSecSuccess, let key = item as! SecKey? { return key }
    if status != errSecItemNotFound {
      throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
    }
    var err: Unmanaged<CFError>?
    guard
      let ac = SecAccessControlCreateWithFlags(
        nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, [.privateKeyUsage, .userPresence], &err)
    else { throw err!.takeRetainedValue() }
    let attrs: [String: Any] = [
      kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
      kSecAttrKeySizeInBits as String: 256, kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
      kSecPrivateKeyAttrs as String: [
        kSecAttrIsPermanent as String: true, kSecAttrApplicationTag as String: tag,
        kSecAttrAccessControl as String: ac,
      ],
    ]
    guard let key = SecKeyCreateRandomKey(attrs as CFDictionary, &err) else {
      throw err!.takeRetainedValue()
    }
    return key
  }
  private func pem(for key: SecKey) throws -> Data {
    guard let pub = SecKeyCopyPublicKey(key) else { throw NSError(domain: "ApprovalGUI", code: 30) }
    var err: Unmanaged<CFError>?
    guard let raw = SecKeyCopyExternalRepresentation(pub, &err) as Data? else {
      throw err!.takeRetainedValue()
    }
    let prefix = Data([
      0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01, 0x06, 0x08,
      0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
    ])
    let der = prefix + raw
    let b64 = der.base64EncodedString(options: [.lineLength64Characters, .endLineWithLineFeed])
    return Data(("-----BEGIN PUBLIC KEY-----\n" + b64 + "\n-----END PUBLIC KEY-----\n").utf8)
  }
  func provision(context: LAContext) throws -> String {
    let privateKey = try key(context: context)
    let pemData = try pem(for: privateKey)
    if FileManager.default.fileExists(atPath: publicKeyURL.path) {
      let current = try Data(contentsOf: publicKeyURL)
      guard current == pemData else {
        throw NSError(
          domain: "ApprovalGUI", code: 31,
          userInfo: [NSLocalizedDescriptionKey: "Secure Enclave public key changed unexpectedly."])
      }
    } else {
      let tmp = publicKeyURL.deletingLastPathComponent().appendingPathComponent(
        ".approver-public-key.\(UUID().uuidString).tmp")
      try pemData.write(to: tmp, options: .withoutOverwriting)
      try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: tmp.path)
      try FileManager.default.moveItem(at: tmp, to: publicKeyURL)
    }
    return sha256Hex(pemData)
  }
  func sign(_ req: ApprovalRequest, context: LAContext) throws -> (String, String) {
    let privateKey = try key(context: context)
    let pemData = try pem(for: privateKey)
    if FileManager.default.fileExists(atPath: publicKeyURL.path) {
      let current = try Data(contentsOf: publicKeyURL)
      guard current == pemData else {
        throw NSError(
          domain: "ApprovalGUI", code: 31,
          userInfo: [NSLocalizedDescriptionKey: "Secure Enclave public key changed unexpectedly."])
      }
    } else {
      let tmp = publicKeyURL.deletingLastPathComponent().appendingPathComponent(
        ".approver-public-key.\(UUID().uuidString).tmp")
      try pemData.write(to: tmp, options: .withoutOverwriting)
      try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: tmp.path)
      try FileManager.default.moveItem(at: tmp, to: publicKeyURL)
    }
    let message =
      "local-executor-approval-v1\n\(req.bridge_instance_id)\n\(req.request_id)\n\(req.nonce)\n\(req.payload_sha256)\n\(req.expires_at)\n"
    var err: Unmanaged<CFError>?
    guard
      let sig = SecKeyCreateSignature(
        privateKey, .ecdsaSignatureMessageX962SHA256, Data(message.utf8) as CFData, &err) as Data?
    else { throw err!.takeRetainedValue() }
    return (sha256Hex(pemData), sig.base64EncodedString())
  }
}

final class ApprovalStore {
  let root: URL, pending: URL, decisions: URL
  private let decoder = JSONDecoder(), encoder = JSONEncoder()
  let signer: ApprovalSigner
  init(root: URL) throws {
    self.root = root
    pending = root.appendingPathComponent("pending", isDirectory: true)
    decisions = root.appendingPathComponent("decisions", isDirectory: true)
    signer = ApprovalSigner(root: root)
    for u in [root, pending, decisions] {
      try FileManager.default.createDirectory(at: u, withIntermediateDirectories: true)
      try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: u.path)
    }
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
  }
  func pendingRequests(now: Double = Date().timeIntervalSince1970) -> [ApprovalRequest] {
    guard
      let urls = try? FileManager.default.contentsOfDirectory(
        at: pending, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey],
        options: [.skipsHiddenFiles])
    else { return [] }
    return urls.compactMap { u in
      guard u.pathExtension == "json",
        let v = try? u.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]),
        v.isRegularFile == true, v.isSymbolicLink != true, let d = try? Data(contentsOf: u),
        let r = try? decoder.decode(ApprovalRequest.self, from: d), r.protocol == 1, r.schema == 1,
        r.kind == "operator_approval_request", validRequestID(r.request_id),
        u.lastPathComponent == r.request_id + ".json", validHex(r.nonce, count: 64),
        validHex(r.payload_sha256, count: 64), r.expires_at > now
      else { return nil }
      let du = decisions.appendingPathComponent(r.request_id + ".json")
      return FileManager.default.fileExists(atPath: du.path) ? nil : r
    }.sorted { $0.expires_at < $1.expires_at }
  }
  func decide(_ req: ApprovalRequest, decision: String, signerData: (String, String)? = nil) throws
  {
    guard decision == "allow" || decision == "cancel" else {
      throw NSError(domain: "ApprovalGUI", code: 10)
    }
    let pu = pending.appendingPathComponent(req.request_id + ".json")
    let values = try pu.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
    guard values.isRegularFile == true, values.isSymbolicLink != true else {
      throw NSError(domain: "ApprovalGUI", code: 11)
    }
    let cur = try decoder.decode(ApprovalRequest.self, from: Data(contentsOf: pu))
    guard cur == req else {
      throw NSError(
        domain: "ApprovalGUI", code: 12,
        userInfo: [NSLocalizedDescriptionKey: "Approval request changed; reload before deciding."])
    }
    guard cur.expires_at > Date().timeIntervalSince1970 else {
      throw NSError(
        domain: "ApprovalGUI", code: 13,
        userInfo: [NSLocalizedDescriptionKey: "Approval request expired."])
    }
    if decision == "allow" && signerData == nil {
      throw NSError(
        domain: "ApprovalGUI", code: 14,
        userInfo: [NSLocalizedDescriptionKey: "Signed authorization is required."])
    }
    let out = ApprovalDecision(
      protocol: 1, schema: 1, kind: "operator_approval_decision", request_id: cur.request_id,
      bridge_instance_id: cur.bridge_instance_id, nonce: cur.nonce,
      payload_sha256: cur.payload_sha256, decision: decision,
      decided_at: Date().timeIntervalSince1970, client: "mac_gui",
      signer_key_id: signerData?.0 ?? "",
      signature_algorithm: signerData == nil ? "none" : "ecdsa-p256-sha256",
      signature_b64: signerData?.1 ?? "",
      step_up: [
        "performed": .bool(signerData != nil),
        "policy": .string(signerData == nil ? "none" : "deviceOwnerAuthentication"),
        "result": .string(signerData == nil ? "not_required" : "success"),
      ])
    let target = decisions.appendingPathComponent(cur.request_id + ".json")
    guard !FileManager.default.fileExists(atPath: target.path) else {
      throw NSError(
        domain: "ApprovalGUI", code: 15,
        userInfo: [NSLocalizedDescriptionKey: "A decision already exists for this request."])
    }
    let tmp = decisions.appendingPathComponent(
      "." + cur.request_id + "." + UUID().uuidString + ".tmp")
    try encoder.encode(out).write(to: tmp, options: .withoutOverwriting)
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: tmp.path)
    try FileManager.default.moveItem(at: tmp, to: target)
  }
}

final class ApprovalWindowController: NSWindowController {
  let store: ApprovalStore, request: ApprovalRequest, completion: () -> Void
  let errorLabel = NSTextField(labelWithString: "")
  var approveButton: NSButton!
  init(store: ApprovalStore, request: ApprovalRequest, completion: @escaping () -> Void) {
    self.store = store
    self.request = request
    self.completion = completion
    let w = NSWindow(
      contentRect: NSRect(x: 0, y: 0, width: 720, height: 590),
      styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false
    )
    w.title = "Local Executor Bridge Approval"
    w.center()
    w.isReleasedWhenClosed = false
    super.init(window: w)
    buildUI()
  }
  required init?(coder: NSCoder) { fatalError() }
  func label(_ text: String, bold: Bool = false) -> NSTextField {
    let f = NSTextField(labelWithString: text)
    f.lineBreakMode = .byWordWrapping
    f.maximumNumberOfLines = 0
    f.font =
      bold
      ? .boldSystemFont(ofSize: NSFont.systemFontSize) : .systemFont(ofSize: NSFont.systemFontSize)
    return f
  }
  func buildUI() {
    guard let c = window?.contentView else { return }
    let stack = NSStackView()
    stack.orientation = .vertical
    stack.alignment = .leading
    stack.spacing = 9
    stack.translatesAutoresizingMaskIntoConstraints = false
    c.addSubview(stack)
    stack.addArrangedSubview(label("Approval required", bold: true))
    stack.addArrangedSubview(label(request.explanation))
    stack.addArrangedSubview(label("Category: \(request.category)"))
    stack.addArrangedSubview(label("Working directory: \(request.cwd)"))
    stack.addArrangedSubview(label("Write scope: \(request.requested_write_scope)"))
    stack.addArrangedSubview(
      label("Network authority: \(request.network_authority ? "allowed" : "denied")"))
    stack.addArrangedSubview(label("Effect: \(request.category) in \(request.cwd)"))
    stack.addArrangedSubview(label("Request: \(request.request_id)"))
    stack.addArrangedSubview(label("Fingerprint: \(request.payload_sha256)"))
    stack.addArrangedSubview(label("Exact command", bold: true))
    let tv = NSTextView()
    tv.isEditable = false
    tv.isSelectable = true
    tv.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
    tv.string = request.command
    let scroll = NSScrollView()
    scroll.hasVerticalScroller = true
    scroll.borderType = .bezelBorder
    scroll.documentView = tv
    scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 200).isActive = true
    stack.addArrangedSubview(scroll)
    scroll.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
    errorLabel.textColor = .systemRed
    errorLabel.maximumNumberOfLines = 0
    stack.addArrangedSubview(errorLabel)
    let buttons = NSStackView()
    buttons.orientation = .horizontal
    buttons.spacing = 12
    let reject = NSButton(title: "Reject", target: self, action: #selector(rejectRequest))
    approveButton = NSButton(title: "Approve once", target: self, action: #selector(approveRequest))
    approveButton.keyEquivalent = ""
    buttons.addArrangedSubview(reject)
    buttons.addArrangedSubview(approveButton)
    stack.addArrangedSubview(buttons)
    NSLayoutConstraint.activate([
      stack.leadingAnchor.constraint(equalTo: c.leadingAnchor, constant: 20),
      stack.trailingAnchor.constraint(equalTo: c.trailingAnchor, constant: -20),
      stack.topAnchor.constraint(equalTo: c.topAnchor, constant: 20),
      stack.bottomAnchor.constraint(lessThanOrEqualTo: c.bottomAnchor, constant: -20),
    ])
  }
  func fail(_ error: Error) {
    errorLabel.stringValue = (error as NSError).localizedDescription
    approveButton.isEnabled = true
  }
  @objc func rejectRequest() {
    do {
      try store.decide(request, decision: "cancel")
      close()
      completion()
    } catch { fail(error) }
  }
  @objc func approveRequest() {
    approveButton.isEnabled = false
    errorLabel.stringValue = "Authenticating…"
    let ctx = LAContext()
    ctx.localizedReason =
      "Approve this Local Executor Bridge action once: \(String(request.explanation.prefix(180)))"
    ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: ctx.localizedReason) {
      [weak self] ok, error in
      DispatchQueue.main.async {
        guard let self = self else { return }
        guard ok else {
          self.fail(
            error
              ?? NSError(
                domain: "ApprovalGUI", code: 20,
                userInfo: [NSLocalizedDescriptionKey: "Authentication was cancelled."]))
          return
        }
        do {
          guard self.request.expires_at > Date().timeIntervalSince1970 else {
            throw NSError(
              domain: "ApprovalGUI", code: 21,
              userInfo: [NSLocalizedDescriptionKey: "Approval request expired."])
          }
          let signed = try self.store.signer.sign(self.request, context: ctx)
          try self.store.decide(self.request, decision: "allow", signerData: signed)
          self.close()
          self.completion()
        } catch { self.fail(error) }
      }
    }
  }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
  var statusItem: NSStatusItem!, store: ApprovalStore!, fallbackTimer: Timer?,
    source: DispatchSourceFileSystemObject?, watchFD: Int32 = -1,
    windows: [String: ApprovalWindowController] = [:], seen = Set<String>()
  func applicationDidFinishLaunching(_ notification: Notification) {
    NSApp.setActivationPolicy(.accessory)
    guard let root = Bundle.main.object(forInfoDictionaryKey: "ApprovalRoot") as? String,
      !root.isEmpty
    else { fatalError("ApprovalRoot missing") }
    store = try! ApprovalStore(root: URL(fileURLWithPath: root, isDirectory: true))
    statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    statusItem.button?.title = "Bridge"
    statusItem.button?.toolTip = "Local Executor Bridge approvals"
    installWatcher()
    refresh(showNew: true)
    fallbackTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
      self?.refresh(showNew: true)
    }
  }
  func installWatcher() {
    watchFD = open(store.pending.path, O_EVTONLY)
    guard watchFD >= 0 else { return }
    let s = DispatchSource.makeFileSystemObjectSource(
      fileDescriptor: watchFD, eventMask: [.write, .extend, .attrib, .rename, .delete], queue: .main
    )
    s.setEventHandler { [weak self] in self?.refresh(showNew: true) }
    s.setCancelHandler { [fd = watchFD] in if fd >= 0 { close(fd) } }
    s.resume()
    source = s
  }
  func refresh(showNew: Bool) {
    let rs = store.pendingRequests()
    let ids = Set(rs.map { $0.request_id })
    let newIDs = ids.subtracting(seen)
    seen = ids
    statusItem.button?.title = rs.isEmpty ? "Bridge" : "Bridge \(rs.count)"
    let m = NSMenu()
    if rs.isEmpty {
      let i = NSMenuItem(title: "No pending approvals", action: nil, keyEquivalent: "")
      i.isEnabled = false
      m.addItem(i)
    } else {
      for r in rs {
        let title =
          r.explanation.count > 64 ? String(r.explanation.prefix(61)) + "…" : r.explanation
        let i = NSMenuItem(title: title, action: #selector(openFromMenu(_:)), keyEquivalent: "")
        i.target = self
        i.representedObject = r.request_id
        m.addItem(i)
      }
    }
    m.addItem(.separator())
    let q = NSMenuItem(title: "Quit Approval UI", action: #selector(quitApp), keyEquivalent: "q")
    q.target = self
    q.isEnabled = rs.isEmpty
    m.addItem(q)
    statusItem.menu = m
    if showNew, let id = newIDs.sorted().first, let r = rs.first(where: { $0.request_id == id }) {
      show(r)
    }
  }
  @objc func openFromMenu(_ sender: NSMenuItem) {
    guard let id = sender.representedObject as? String,
      let r = store.pendingRequests().first(where: { $0.request_id == id })
    else { return }
    show(r)
  }
  func show(_ r: ApprovalRequest) {
    let c =
      windows[r.request_id]
      ?? ApprovalWindowController(store: store, request: r) { [weak self] in
        self?.windows.removeValue(forKey: r.request_id)
        self?.refresh(showNew: false)
      }
    c.window?.delegate = self
    windows[r.request_id] = c
    c.showWindow(nil)
    c.window?.makeKeyAndOrderFront(nil)
    NSApp.activate(ignoringOtherApps: true)
  }
  func windowWillClose(_ notification: Notification) {
    guard let w = notification.object as? NSWindow,
      let p = windows.first(where: { $0.value.window === w })
    else { return }
    windows.removeValue(forKey: p.key)
  }
  @objc func quitApp() { NSApp.terminate(nil) }
}

func provisionKey() -> Int32 {
  do {
    guard let rawRoot = ProcessInfo.processInfo.environment["APPROVAL_PROVISION_ROOT"], !rawRoot.isEmpty else {
      fputs("APPROVAL_PROVISION_ROOT is required\n", stderr)
      return 23
    }
    let root = URL(fileURLWithPath: rawRoot, isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    let signer = ApprovalSigner(root: root)
    let keyID = try signer.provision(context: LAContext())
    guard validHex(keyID, count: 64) else { return 24 }
    print(keyID)
    return 0
  } catch {
    fputs("key provisioning failed: \(error)\n", stderr)
    return 25
  }
}

func selfTest() -> Int32 {
  do {
    let golden =
      #"{"authority_summary":{"effective_write_scope":{"allow_network":true,"effective":"system","requested":"system"},"network_authority":true,"requested_write_scope":"system"},"bridge_instance_id":"golden-instance","category":"system_write","command":"printf ok","cwd":"/tmp/repo","effect_summary":{"category":"system_write","cwd":"/tmp/repo"},"effective_write_scope":{"allow_network":true,"effective":"system","requested":"system"},"explanation":"Golden approval vector","network_authority":true,"nonce":"abababababababababababababababababababababababababababababababab","request_id":"golden-req","requested_write_scope":"system","schema":1}"#
    guard
      sha256Hex(Data(golden.utf8))
        == "9765cc265f2b6723127625d664f7f8849454a4f223a471e786d55ce2fd432506"
    else { return 19 }
    let base =
      ProcessInfo.processInfo.environment["APPROVAL_SELF_TEST_ROOT"]
      ?? FileManager.default.currentDirectoryPath
    let root = URL(fileURLWithPath: base, isDirectory: true).appendingPathComponent(
      "approval-selftest-\(UUID().uuidString)", isDirectory: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let store = try ApprovalStore(root: root)
    let req = ApprovalRequest(
      protocol: 1, schema: 1, kind: "operator_approval_request", request_id: "self-test",
      bridge_instance_id: "self-test-instance", nonce: String(repeating: "a", count: 64),
      payload_sha256: String(repeating: "b", count: 64), category: "self_test",
      explanation: "Self-test", cwd: "/tmp", command: "true", requested_write_scope: "read_only",
      effective_write_scope: [:], network_authority: false, authority_summary: [:],
      effect_summary: [:], expires_at: Date().timeIntervalSince1970 + 60)
    try JSONEncoder().encode(req).write(to: store.pending.appendingPathComponent("self-test.json"))
    guard store.pendingRequests().count == 1 else { return 20 }
    try store.decide(req, decision: "cancel")
    let out = try JSONDecoder().decode(
      ApprovalDecision.self,
      from: Data(contentsOf: store.decisions.appendingPathComponent("self-test.json")))
    return out.request_id == req.request_id && out.decision == "cancel" && out.client == "mac_gui"
      && out.signer_key_id.isEmpty && out.signature_algorithm == "none" && out.signature_b64.isEmpty
      && out.step_up["performed"] == .bool(false) ? 0 : 21
  } catch {
    fputs("self-test failed: \(error)\n", stderr)
    return 22
  }
}
if CommandLine.arguments.contains("--provision-key") { exit(provisionKey()) }
if CommandLine.arguments.contains("--self-test") { exit(selfTest()) }
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
