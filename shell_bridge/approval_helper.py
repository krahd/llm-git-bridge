#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path

SCRIPT = r'''ObjC.import("AppKit");
function run(argv) {
  const requestId=argv[0], categoryName=argv[1], cwdValue=argv[2], commandValue=argv[3], explanationValue=argv[4];
  const app=$.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);
  const alert=$.NSAlert.alloc.init;
  alert.setMessageText("Allow ChatGPT to perform this action?");
  alert.setInformativeText("What ChatGPT is trying to do:\n"+explanationValue+"\n\nWhy approval is required:\n"+categoryName.replace(/_/g," "));
  alert.setAlertStyle($.NSAlertStyleWarning);
  alert.addButtonWithTitle("Cancel"); alert.addButtonWithTitle("Allow");
  const detailText="Request: "+requestId+"\nWorking directory: "+cwdValue+"\n\nCommand:\n"+commandValue;
  const textView=$.NSTextView.alloc.initWithFrame($.NSMakeRect(0,0,1060,300));
  textView.setString(detailText); textView.setEditable(false); textView.setSelectable(true);
  textView.setFont($.NSFont.monospacedSystemFontOfSizeWeight(12,$.NSFontWeightRegular));
  const scrollView=$.NSScrollView.alloc.initWithFrame($.NSMakeRect(0,0,1080,320));
  scrollView.setDocumentView(textView); scrollView.setHasVerticalScroller(true); scrollView.setHasHorizontalScroller(true);
  scrollView.setAutohidesScrollers(true); scrollView.setBorderType($.NSBezelBorder); alert.setAccessoryView(scrollView);
  app.activateIgnoringOtherApps(true);
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
