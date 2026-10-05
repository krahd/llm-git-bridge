import AppKit
import Foundation
import LocalAuthentication

let expectedRoot = "/Users/tom/tom-repos/projects/.worktrees/f957904978c99f4c/v6-approval-foreground-20261004-a1"
let expectedLabel = "net.laurenzo.local-executor-bridge-v6-staging"
let expectedDriveRoot = "15ql2yACOq7H6qo0IgzosySqW8nHgUKXv"
let repairScript = expectedRoot + "/shell_bridge/repair_staging.sh"

func fail(_ message: String) -> Never {
    let alert = NSAlert()
    alert.messageText = "Local Executor Bridge Repair"
    alert.informativeText = message
    alert.alertStyle = .critical
    alert.runModal()
    exit(1)
}

func run(_ exe: String, _ args: [String], env: [String:String]? = nil) throws -> String {
    let p = Process(); p.executableURL = URL(fileURLWithPath: exe); p.arguments = args
    if let env { p.environment = env }
    let out = Pipe(); let err = Pipe(); p.standardOutput = out; p.standardError = err
    try p.run(); p.waitUntilExit()
    let data = out.fileHandleForReading.readDataToEndOfFile() + err.fileHandleForReading.readDataToEndOfFile()
    let s = String(data: data, encoding: .utf8) ?? ""
    if p.terminationStatus != 0 { throw NSError(domain:"Repair", code:Int(p.terminationStatus), userInfo:[NSLocalizedDescriptionKey:s]) }
    return s
}

let repoURL = URL(fileURLWithPath: expectedRoot)
let head: String
do {
    head = try run("/usr/bin/git", ["-C", expectedRoot, "rev-parse", "HEAD"]).trimmingCharacters(in: .whitespacesAndNewlines)
} catch { fail("Cannot read candidate Git commit: \\(error)") }

guard head.count == 40 else { fail("Candidate Git commit is invalid.") }

guard FileManager.default.fileExists(atPath: repairScript) else { fail("Repair script is missing from the audited candidate.") }

let message = "Upgrade only the isolated Local Executor Bridge v6 staging runtime.\n\nCandidate: \\(head)\nService: \\(expectedLabel)\nDrive root: \\(expectedDriveRoot)\n\nProduction v5 services will not be stopped or modified."
let alert = NSAlert(); alert.messageText = "Repair isolated v6 staging bridge"; alert.informativeText = message
alert.addButton(withTitle: "Authenticate and Repair")
alert.addButton(withTitle: "Cancel")
if alert.runModal() != .alertFirstButtonReturn { exit(2) }

let ctx = LAContext(); ctx.localizedReason = "Authorize the isolated v6 staging bridge repair"
let sem = DispatchSemaphore(value: 0); var authOK = false; var authErr: Error?
ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: ctx.localizedReason) { ok, err in authOK = ok; authErr = err; sem.signal() }
sem.wait()
guard authOK else { fail("Device-owner authentication failed or was cancelled: \\(String(describing: authErr))") }

var env = ProcessInfo.processInfo.environment
env["EXPECTED_SOURCE_COMMIT"] = head
env["EXPECTED_STAGING_LABEL"] = expectedLabel
env["EXPECTED_STAGING_DRIVE_ROOT"] = expectedDriveRoot
do {
    let result = try run("/bin/bash", [repairScript], env: env)
    let ok = NSAlert(); ok.messageText = "Staging bridge repaired"; ok.informativeText = result.suffix(3000).description; ok.runModal()
} catch { fail("Repair failed safely. Previous staging runtime should have been restored.\n\n\\(error.localizedDescription)") }
