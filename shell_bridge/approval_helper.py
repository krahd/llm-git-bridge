#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path

SCRIPT = r'''ObjC.import("AppKit");
function run(argv) {
  const requestId=argv[0], categoryName=argv[1], cwdValue=argv[2], commandValue=argv[3], explanationValue=argv[4];
  const app=$.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  app.finishLaunching();
  const alert=$.NSAlert.alloc.init;
  const riskDescriptions = {
    system_write: "This request can write outside the current Git repository or change system-level state.",
    non_repository_filesystem_mutation: "This request can modify files outside an existing Git repository.",
    github_repository_control_plane_mutation: "This request can change GitHub repository settings or other control-plane state.",
    github_content_mutation: "This request can change content or metadata on GitHub.",
    mutating_network_request: "This request can send a network operation that changes remote state.",
    remote_shell_or_copy: "This request can connect to another machine or copy files over the network.",
    git_force_or_delete_push: "This request can rewrite or delete remote Git history.",
    destructive_git_reset: "This request can discard local Git changes or commits.",
    destructive_git_clean: "This request can delete untracked files.",
    recursive_forced_delete: "This request can recursively delete files."
  };
  const riskDescription = riskDescriptions[categoryName] || ("Bridge-detected risk: " + categoryName.replace(/_/g, " ") + ".");
  alert.setMessageText("ChatGPT is asking to cross a safety boundary");
  alert.setInformativeText("Requested action:\n"+explanationValue+"\n\nSafety reason:\n"+riskDescription+"\n\nWorking directory:\n"+cwdValue+"\n\nNo action is taken until you make a deliberate choice. Escape cancels; Command-Return allows.");
  alert.setAlertStyle($.NSAlertStyleWarning);
  const cancelButton=alert.addButtonWithTitle("Cancel");
  const allowButton=alert.addButtonWithTitle("Allow");
  cancelButton.setKeyEquivalent("\u001b");
  allowButton.setKeyEquivalent("\r");
  allowButton.setKeyEquivalentModifierMask($.NSEventModifierFlagCommand);
  const detailText="Technical details (exact request)\n\nRequest: "+requestId+"\nRisk category: "+categoryName+"\n\nExact command:\n"+commandValue;
  const textView=$.NSTextView.alloc.initWithFrame($.NSMakeRect(0,0,820,180));
  textView.setString(detailText); textView.setEditable(false); textView.setSelectable(true);
  textView.setFont($.NSFont.monospacedSystemFontOfSizeWeight(12,$.NSFontWeightRegular));
  const scrollView=$.NSScrollView.alloc.initWithFrame($.NSMakeRect(0,0,840,200));
  scrollView.setDocumentView(textView); scrollView.setHasVerticalScroller(true); scrollView.setHasHorizontalScroller(true);
  scrollView.setAutohidesScrollers(true); scrollView.setBorderType($.NSBezelBorder); alert.setAccessoryView(scrollView);
  const window=alert.window;
  window.setLevel($.NSModalPanelWindowLevel);
  window.setHidesOnDeactivate(false);
  window.setCollectionBehavior($.NSWindowCollectionBehaviorCanJoinAllSpaces | $.NSWindowCollectionBehaviorFullScreenAuxiliary);
  app.activateIgnoringOtherApps(true);
  window.makeKeyAndOrderFront(null);
  window.orderFrontRegardless();
  window.makeFirstResponder(textView);
  const response=alert.runModal;
  return response === $.NSAlertSecondButtonReturn ? "allow" : "cancel";
}
'''

def atomic_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(tmp, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, sort_keys=True, separators=(",", ":")); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass

def main() -> int:
    if len(sys.argv) != 3: return 2
    pending_path, decision_path = map(Path, sys.argv[1:])
    try: req = json.loads(pending_path.read_text(encoding="utf-8"))
    except Exception: return 3
    required=("request_id","nonce","payload_sha256","category","cwd","command","explanation","expires_at")
    if req.get("protocol") != 1 or req.get("kind") != "operator_approval_request" or any(k not in req for k in required): return 4
    remaining = float(req["expires_at"]) - time.time()
    if remaining <= 0: return 5
    try:
        cp=subprocess.run(["/usr/bin/osascript","-l","JavaScript","-e",SCRIPT,"--",req["request_id"],req["category"],req["cwd"],req["command"],req["explanation"]],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=max(1,min(remaining,300)),check=False)
        decision="allow" if cp.returncode==0 and cp.stdout.strip()=="allow" else "cancel"
    except Exception:
        decision="cancel"
    atomic_json(decision_path,{"protocol":1,"kind":"operator_approval_decision","request_id":req["request_id"],"nonce":req["nonce"],"payload_sha256":req["payload_sha256"],"decision":decision,"decided_at":time.time()})
    return 0

if __name__ == "__main__": raise SystemExit(main())
