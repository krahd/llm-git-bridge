#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path

JXA = r'''ObjC.import("AppKit");
ObjC.import("Foundation");
const approvalRoot = ObjC.unwrap($.NSProcessInfo.processInfo.arguments.objectAtIndex(4));
const helperPath = ObjC.unwrap($.NSProcessInfo.processInfo.arguments.objectAtIndex(5));
const versionLabel = ObjC.unwrap($.NSProcessInfo.processInfo.arguments.objectAtIndex(6));
const buildId = ObjC.unwrap($.NSProcessInfo.processInfo.arguments.objectAtIndex(7));
let statusItem = null;
let menu = null;
let known = {};
let firstRefresh = true;
let hasBridgeImage = false;

function unwrap(v) { try { return ObjC.unwrap(v); } catch (e) { return v; } }
function readJSON(path) {
  try {
    const s = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!s) return null;
    return JSON.parse(unwrap(s));
  } catch (e) { return null; }
}
function pendingRecords() {
  const fm = $.NSFileManager.defaultManager;
  const dir = approvalRoot + "/pending";
  const namesObj = fm.contentsOfDirectoryAtPathError(dir, null);
  const names = [];
  if (namesObj) {
    const count = Number(namesObj.count);
    for (let i = 0; i < count; i++) names.push(unwrap(namesObj.objectAtIndex(i)));
  }
  const now = Date.now()/1000;
  const out = [];
  for (const name of names) {
    if (!String(name).endsWith(".json")) continue;
    const path = dir + "/" + String(name);
    const req = readJSON(path);
    if (!req || req.kind !== "operator_approval_request" || Number(req.expires_at||0) <= now) continue;
    req.__pendingPath = path;
    req.__decisionPath = approvalRoot + "/decisions/" + req.request_id + ".json";
    out.push(req);
  }
  out.sort((a,b) => Number(a.created_at||0) - Number(b.created_at||0));
  return out;
}
function decide(req, decision) {
  try {
    const task = $.NSTask.alloc.init;
    task.launchPath = "/usr/bin/python3";
    task.arguments = [helperPath, "--decide", req.__pendingPath, req.__decisionPath, decision];
    task.launch;
    task.waitUntilExit;
  } catch (e) {}
}
function review(req) {
  const app = $.NSApplication.sharedApplication;
  const alert = $.NSAlert.alloc.init;
  alert.messageText = "Allow ChatGPT to perform this action?";
  alert.informativeText = "What ChatGPT is trying to do:\n" + String(req.explanation||"") + "\n\nWhy approval is required:\n" + String(req.category||"").replace(/_/g," ");
  alert.alertStyle = $.NSAlertStyleWarning;
  alert.addButtonWithTitle("Reject");
  alert.addButtonWithTitle("Allow");
  const details = "Request: " + req.request_id + "\nWorking directory: " + req.cwd + "\n\nCommand:\n" + req.command;
  const tv = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0,0,820,240));
  tv.string = details; tv.editable = false; tv.selectable = true;
  tv.font = $.NSFont.monospacedSystemFontOfSizeWeight(11,$.NSFontWeightRegular);
  const sv = $.NSScrollView.alloc.initWithFrame($.NSMakeRect(0,0,840,260));
  sv.documentView = tv; sv.hasVerticalScroller = true; sv.hasHorizontalScroller = true; sv.autohidesScrollers = true; sv.borderType = $.NSBezelBorder;
  alert.accessoryView = sv;
  app.activateIgnoringOtherApps(true);
  const response = alert.runModal;
  decide(req, response === $.NSAlertSecondButtonReturn ? "allow" : "cancel");
}
function shortText(s,n) { s=String(s||"").replace(/\s+/g," ").trim(); return s.length>n ? s.slice(0,n-1)+"…" : s; }
function setStatusCount(count) {
  if (hasBridgeImage) statusItem.button.title = count > 0 ? " " + count : "";
  else statusItem.button.title = count > 0 ? "🌉 " + count : "🌉";
}
function showAbout() {
  const alert = $.NSAlert.alloc.init;
  alert.messageText = "Local Executor Bridge";
  alert.informativeText = "Version: " + versionLabel + "\nBuild: " + buildId + "\n\nApproval queue: click-only Allow / Reject; no identity authentication.";
  alert.alertStyle = $.NSAlertStyleInformational;
  alert.addButtonWithTitle("OK");
  $.NSApplication.sharedApplication.activateIgnoringOtherApps(true);
  alert.runModal;
}


ObjC.registerSubclass({
  name: "LEBApprovalQueueDelegate",
  methods: {
    "refresh:": { types:["void",["id"]], implementation:function(sender) {
      const records = pendingRecords();
      menu.removeAllItems;
      const heading = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent("Local Executor Bridge " + versionLabel, null, "");
      heading.enabled = false; menu.addItem(heading);
      const build = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent("Build: " + buildId, null, "");
      build.enabled = false; menu.addItem(build);
      menu.addItem($.NSMenuItem.separatorItem);
      if (records.length === 0) {
        const empty = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent("No approvals pending", null, ""); empty.enabled=false; menu.addItem(empty);
        setStatusCount(0);
      } else {
        setStatusCount(records.length);
        for (const req of records) {
          const item = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent(shortText(req.explanation || req.request_id, 72), "review:", "");
          item.target = this; item.representedObject = $(req.request_id); menu.addItem(item);
        }
      }
      menu.addItem($.NSMenuItem.separatorItem);
      const about = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent("About Local Executor Bridge", "about:", ""); about.target=this; menu.addItem(about);
      menu.addItem($.NSMenuItem.separatorItem);
      const quit = $.NSMenuItem.alloc.initWithTitleActionKeyEquivalent("Quit approval queue", "quit:", ""); quit.target=this; menu.addItem(quit);
      const current = {};
      for (const req of records) current[req.request_id]=true;
      let firstNew = null;
      for (const req of records) if (!known[req.request_id] && !firstNew) firstNew=req;
      known = current;
      if (firstNew) review(firstNew);
      firstRefresh = false;
    }},
    "review:": { types:["void",["id"]], implementation:function(sender) {
      const rid = unwrap(sender.representedObject);
      for (const req of pendingRecords()) if (req.request_id === rid) { review(req); break; }
      this['refresh:'](sender);
    }},
    "about:": { types:["void",["id"]], implementation:function(sender) { showAbout(); }},
    "quit:": { types:["void",["id"]], implementation:function(sender) { $.NSApplication.sharedApplication.terminate(null); }}
  }
});
const Delegate = $.LEBApprovalQueueDelegate;

const app = $.NSApplication.sharedApplication;
app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
const delegate = Delegate.alloc.init;
app.delegate = delegate;
statusItem = $.NSStatusBar.systemStatusBar.statusItemWithLength($.NSVariableStatusItemLength);
const bridgeImage = $.NSImage.imageWithSystemSymbolNameAccessibilityDescription("bridge", "Local Executor Bridge");
if (bridgeImage) { bridgeImage.template = true; statusItem.button.image = null; button.title = "🌉"; statusItem.button.imagePosition = $.NSImageLeft; hasBridgeImage = true; }
setStatusCount(0);
statusItem.button.toolTip = "Local Executor Bridge " + versionLabel + " (" + buildId + ")";
menu = $.NSMenu.alloc.initWithTitle("Approvals");
statusItem.menu = menu;
delegate['refresh:'](null);
$.NSTimer.scheduledTimerWithTimeIntervalTargetSelectorUserInfoRepeats(1.0, delegate, "refresh:", null, true);
app.run;
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

def load_pending(path: Path) -> dict | None:
    try: req = json.loads(path.read_text(encoding="utf-8"))
    except Exception: return None
    required=("request_id","nonce","payload_sha256","category","cwd","command","explanation","expires_at")
    if req.get("protocol") != 1 or req.get("kind") != "operator_approval_request" or any(k not in req for k in required): return None
    if float(req["expires_at"]) <= time.time(): return None
    return req

def decide(pending_path: Path, decision_path: Path, decision: str) -> int:
    req = load_pending(pending_path)
    if req is None or decision not in {"allow","cancel"}: return 4
    atomic_json(decision_path,{"protocol":1,"kind":"operator_approval_decision","request_id":req["request_id"],"nonce":req["nonce"],"payload_sha256":req["payload_sha256"],"decision":decision,"decided_at":time.time()})
    return 0

def version_info() -> tuple[str, str]:
    version = "v6"
    build = "unknown"
    manifest = Path(__file__).resolve().with_name("install-manifest.json")
    try:
        source_commit = str(json.loads(manifest.read_text(encoding="utf-8")).get("source_commit") or "")
        if source_commit:
            build = source_commit[:12]
    except Exception:
        build = "dev"
    return version, build

def queue(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True); (root/"pending").mkdir(exist_ok=True); (root/"decisions").mkdir(exist_ok=True)
    version, build = version_info()
    cp = subprocess.run(["/usr/bin/osascript","-l","JavaScript","-e",JXA,"--",str(root),str(Path(__file__).resolve()),version,build], check=False)
    return cp.returncode

def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--decide" and len(sys.argv) == 5:
        return decide(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    if len(sys.argv) == 3 and sys.argv[1] == "--queue":
        return queue(Path(sys.argv[2]))
    return 2

if __name__ == "__main__": raise SystemExit(main())
