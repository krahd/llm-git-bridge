const storageKey = (tabId) => `pendingPrompt:${tabId}`;
const PENDING_TTL_MS = 10 * 60 * 1000;
const DELIVERY_TIMEOUT_MS = 20000;

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

function injectedFillPrompt(prompt) {
  const candidates = [
    document.querySelector("textarea#prompt-textarea"),
    document.querySelector("#prompt-textarea[contenteditable='true']"),
    document.querySelector("[data-testid='prompt-textarea'][contenteditable='true']")
  ].filter(Boolean);
  if (candidates.length !== 1) return false;
  const composer = candidates[0];
  composer.focus();
  if (composer instanceof HTMLTextAreaElement) {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    if (!setter) return false;
    setter.call(composer, prompt);
  } else {
    composer.textContent = prompt;
  }
  composer.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: prompt}));
  return true;
}

async function deliverByScripting(tabId, prompt) {
  try {
    const results = await browser.scripting.executeScript({
      target: {tabId},
      func: injectedFillPrompt,
      args: [prompt]
    });
    return Array.isArray(results) && results.some((entry) => entry?.result === true);
  } catch (_) {
    return false;
  }
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
    // The static content script may not be ready yet; use the explicit scripting fallback below.
  }
  if (await deliverByScripting(tabId, prompt)) {
    await clearPending(tabId);
    return true;
  }
  return false;
}

async function waitForTabComplete(tabId, timeoutMs = DELIVERY_TIMEOUT_MS) {
  const initial = await browser.tabs.get(tabId);
  if (initial?.status === "complete") return true;
  return new Promise((resolve) => {
    let finished = false;
    const finish = (value) => {
      if (finished) return;
      finished = true;
      browser.tabs.onUpdated.removeListener(onUpdated);
      clearTimeout(timer);
      resolve(value);
    };
    const onUpdated = (updatedId, changeInfo) => {
      if (updatedId === tabId && changeInfo.status === "complete") finish(true);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    browser.tabs.onUpdated.addListener(onUpdated);
  });
}

browser.runtime.onMessage.addListener(async (message, sender) => {
  if (message?.type === "contentReady" && sender?.tab?.id != null) {
    const filled = await deliverPending(sender.tab.id);
    return {ok: true, filled};
  }
  if (message?.type !== "openPrompt" || typeof message.prompt !== "string") return;
  const tab = await browser.tabs.create({url: "https://chatgpt.com/"});
  await savePending(tab.id, message.prompt);
  await waitForTabComplete(tab.id);
  const filled = await deliverPending(tab.id);
  return filled
    ? {ok: true, filled: true, tabId: tab.id}
    : {ok: false, filled: false, tabId: tab.id, error: "ChatGPT opened, but Safari could not fill the prompt. Confirm Conversation Harness has access to chatgpt.com."};
});

browser.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (changeInfo.status !== "complete") return;
  await deliverPending(tabId);
});

browser.tabs.onRemoved.addListener(async (tabId) => {
  await clearPending(tabId);
});
