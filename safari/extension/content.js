function findComposer() {
  const candidates = [...new Set([
    document.querySelector("textarea#prompt-textarea"),
    document.querySelector("#prompt-textarea[contenteditable='true']"),
    document.querySelector("[data-testid='prompt-textarea'][contenteditable='true']")
  ].filter(Boolean))];
  return candidates.length === 1 ? candidates[0] : null;
}

function fillComposer(prompt) {
  const composer = findComposer();
  if (!composer) return false;
  composer.focus();
  if (composer instanceof HTMLTextAreaElement) {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    setter?.call(composer, prompt);
  } else {
    composer.textContent = prompt;
  }
  composer.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: prompt}));
  return true;
}

function waitAndFillComposer(prompt, timeoutMs = 15000) {
  if (fillComposer(prompt)) return Promise.resolve(true);
  return new Promise((resolve) => {
    let finished = false;
    let timer = null;
    const finish = (filled) => {
      if (finished) return;
      finished = true;
      observer.disconnect();
      if (timer !== null) clearTimeout(timer);
      resolve(filled);
    };
    const observer = new MutationObserver(() => {
      if (fillComposer(prompt)) finish(true);
    });
    observer.observe(document.documentElement, {childList: true, subtree: true});
    timer = setTimeout(() => finish(false), timeoutMs);
    if (fillComposer(prompt)) finish(true);
  });
}

browser.runtime.onMessage.addListener((message) => {
  if (message?.type !== "fillPrompt" || typeof message.prompt !== "string") return;
  return waitAndFillComposer(message.prompt).then((filled) => ({filled}));
});

// Wake the MV3 background worker after this content script is actually ready.
// The background retains the pending prompt until fillPrompt succeeds.
browser.runtime.sendMessage({type: "contentReady"}).catch(() => {});
