const pendingByTab = new Map();

browser.runtime.onMessage.addListener(async (message) => {
  if (message?.type !== "openPrompt" || typeof message.prompt !== "string") return;
  const tab = await browser.tabs.create({url: "https://chatgpt.com/"});
  pendingByTab.set(tab.id, message.prompt);
  return {ok: true, tabId: tab.id};
});

browser.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (changeInfo.status !== "complete" || !pendingByTab.has(tabId)) return;
  const prompt = pendingByTab.get(tabId);
  try {
    const response = await browser.tabs.sendMessage(tabId, {type: "fillPrompt", prompt});
    if (response?.filled) pendingByTab.delete(tabId);
  } catch (_) {
    // Fail closed. The user can retry from the popup after ChatGPT finishes loading.
  }
});

browser.tabs.onRemoved.addListener((tabId) => pendingByTab.delete(tabId));
