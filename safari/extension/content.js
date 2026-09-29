function findComposer() {
  const candidates = [
    document.querySelector("textarea#prompt-textarea"),
    document.querySelector("#prompt-textarea[contenteditable='true']")
  ].filter(Boolean);
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

browser.runtime.onMessage.addListener((message) => {
  if (message?.type !== "fillPrompt" || typeof message.prompt !== "string") return;
  return Promise.resolve({filled: fillComposer(message.prompt)});
});
