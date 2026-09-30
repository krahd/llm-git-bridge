const BASE = "http://127.0.0.1:47653";
const status = document.getElementById("status");
const CHATGPT_ORIGIN = "https://chatgpt.com/*";

async function refreshStatus() {
  try {
    const health = await fetch(BASE + "/health");
    const body = await health.json();
    if (!health.ok || !body.ok) throw new Error("Harness health check failed.");
  } catch (_) {
    status.textContent = "Harness unavailable on this Mac.";
    return;
  }

  const {token} = await browser.storage.local.get("token");
  if (!token) {
    status.textContent = "Harness ready. Pair this browser.";
    return;
  }

  const response = await fetch(BASE + "/v1/pending", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({token})
  });
  if (response.status === 401 || response.status === 403) {
    await browser.storage.local.remove("token");
    status.textContent = "Harness ready. Pair this browser.";
    return;
  }
  const body = await response.json();
  if (!body.ok) throw new Error(body.error || "Harness unavailable");
  const count = body.pending.length;
  status.textContent = `Paired. ${count} pending handoff${count === 1 ? "" : "s"}.`;
}

async function clientId() {
  const stored = await browser.storage.local.get("clientId");
  if (stored.clientId) return stored.clientId;
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  const id = "safari-" + Array.from(bytes, b => b.toString(16).padStart(2,"0")).join("");
  await browser.storage.local.set({clientId: id});
  return id;
}

async function pair() {
  const pairButton = document.getElementById("pair");
  const codeInput = document.getElementById("code");
  const code = codeInput.value.trim();
  if (!code) throw new Error("Enter the pairing code shown by the harness CLI.");
  pairButton.disabled = true;
  status.textContent = "Pairing...";
  try {
    const response = await fetch(BASE + "/pair", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({code, client_id: await clientId()})
    });
    const body = await response.json();
    if (!body.ok) throw new Error(body.error || "Pairing failed");
    await browser.storage.local.set({token: body.token});
    codeInput.value = "";
    status.textContent = "Paired. Verifying...";
    await refreshStatus();
  } finally {
    pairButton.disabled = false;
  }
}

async function releaseStart(token, handoffId) {
  try {
    await fetch(BASE + "/v1/release-start", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({token, handoff_id: handoffId})
    });
  } catch (_) {
    // The short server-side starting lease will recover even if release delivery fails.
  }
}

async function ensureChatGPTAccess() {
  if (await browser.permissions.contains({origins: [CHATGPT_ORIGIN]})) return;
  const granted = await browser.permissions.request({origins: [CHATGPT_ORIGIN]});
  if (!granted) {
    throw new Error("Allow Conversation Harness access to chatgpt.com in Safari, then try again.");
  }
}

async function openNext() {
  const {token} = await browser.storage.local.get("token");
  if (!token) throw new Error("Pair this browser first.");
  await ensureChatGPTAccess();
  const response = await fetch(BASE + "/v1/open-next", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({token})
  });
  const body = await response.json();
  if (!body.ok) throw new Error(body.error || "Harness unavailable");
  const item = body.item;
  if (!item) { status.textContent = "No pending handoffs."; return; }
  if (!item.new_reservation) {
    status.textContent = `Already starting ${item.job_id}; use the existing tab or retry after the reservation expires.`;
    return;
  }
  try {
    const opened = await browser.runtime.sendMessage({type:"openPrompt", prompt:item.prompt});
    if (!opened?.filled) throw new Error(opened?.error || "ChatGPT opened, but Safari could not fill the prompt.");
  } catch (error) {
    await releaseStart(token, item.handoff_id);
    throw error;
  }
  status.textContent = `Opened ${item.job_id}. Review the prompt and press Send.`;
}

document.getElementById("pair").addEventListener("click", () => pair().catch(e => status.textContent=e.message));
document.getElementById("open").addEventListener("click", () => openNext().catch(e => status.textContent=e.message));
refreshStatus().catch(e => status.textContent=e.message);
