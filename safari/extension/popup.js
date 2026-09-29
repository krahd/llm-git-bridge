const BASE = "http://127.0.0.1:47653";
const status = document.getElementById("status");

async function clientId() {
  const stored = await browser.storage.local.get("clientId");
  if (stored.clientId) return stored.clientId;
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  const id = "safari-" + Array.from(bytes, b => b.toString(16).padStart(2,"0")).join("");
  await browser.storage.local.set({clientId: id});
  return id;
}

async function pair() {
  const code = document.getElementById("code").value.trim();
  if (!code) throw new Error("Enter the pairing code shown by the harness CLI.");
  const response = await fetch(BASE + "/pair", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({code, client_id: await clientId()})
  });
  const body = await response.json();
  if (!body.ok) throw new Error(body.error || "Pairing failed");
  await browser.storage.local.set({token: body.token});
  status.textContent = "Paired.";
}

async function releaseStart(token, handoffId) {
  try {
    await fetch(BASE + "/v1/release-start", {
      method: "POST",
      headers: {Authorization:"Bearer "+token, "Content-Type":"application/json"},
      body: JSON.stringify({handoff_id: handoffId})
    });
  } catch (_) {
    // The short server-side starting lease will recover even if release delivery fails.
  }
}

async function openNext() {
  const {token} = await browser.storage.local.get("token");
  if (!token) throw new Error("Pair this browser first.");
  const response = await fetch(BASE + "/v1/open-next", {
    method: "POST",
    headers: {Authorization:"Bearer "+token, "Content-Type":"application/json"},
    body: "{}"
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
    await browser.runtime.sendMessage({type:"openPrompt", prompt:item.prompt});
  } catch (error) {
    await releaseStart(token, item.handoff_id);
    throw error;
  }
  status.textContent = `Opened ${item.job_id}. Review the prompt and press Send.`;
}

document.getElementById("pair").addEventListener("click", () => pair().catch(e => status.textContent=e.message));
document.getElementById("open").addEventListener("click", () => openNext().catch(e => status.textContent=e.message));
