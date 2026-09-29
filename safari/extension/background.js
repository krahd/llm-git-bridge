const storageKey = (tabId) => `pendingPrompt:${tabId}`;
const PENDING_TTL_MS = 10 * 60 * 1000;

async function savePending(tabId, prompt) {
  await browser.storage.local.set({[storageKey(tabId)]: {prompt, createdAt: Date.now()}});
}

async function loadPending(tabId) {
  const key = storageKey(tabId);
  const values = await browser.storage.local.get(key);
  const record = values[key];
  if (!record || typeof record !== "object" || typeof record.prompt !== "string" || !Number.isFinite(record.createdAt)) {
    if (record !== undefined) await browser.storage.local.remove(key);
    return undefined;
  }
  const age = Date.now() - record.createdAt;
  if (age < 0 || age > PENDING_TTL_MS) {
    await browser.storage.local.remove(key);
    return undefined;
  }
  return record.prompt;
}

async function clearPending(tabId) {
  await browser.storage.local.remove(storageKey(tabId));
}

async function deliverPending(tabId) {
  const prompt = await loadPending(tabId);
  if (typeof prompt !== "string") return false;
  try {
    const response = await browser.tabs.sendMessage(tabId, {type: "fillPrompt", prompt});
    if (response?.filled) {
      await clearPending(tabId);
      return true;
    }
  } catch (_) {
    // Fail closed. onUpdated or a later popup action may retry after ChatGPT loads.
  }
  return false;
}

browser.runtime.onMessage.addListener(async (message, sender) => {
  if (message?.type === "contentReady" && sender?.tab?.id != null) {
    await deliverPending(sender.tab.id);
    return {ok: true};
  }
  if (message?.type !== "openPrompt" || typeof message.prompt !== "string") return;
  const tab = await browser.tabs.create({url: "https://chatgpt.com/"});
  await savePending(tab.id, message.prompt);
  await deliverPending(tab.id);
  return {ok: true, tabId: tab.id};
});

browser.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (changeInfo.status !== "complete") return;
  await deliverPending(tabId);
});

browser.tabs.onRemoved.addListener(async (tabId) => {
  await clearPending(tabId);
});
