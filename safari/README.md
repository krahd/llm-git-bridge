# Safari re-entry prototype

This is the browser half of Conversation Harness v1. It is deliberately an **actuator, not an authority**.

The extension uses a narrow authenticated loopback API on `127.0.0.1:47653`. Earlier architecture drafts proposed Safari native messaging directly to the harness Unix socket. That was rejected after adversarial review because Safari's native app extension is sandboxed; Apple documents app groups as the supported data-sharing mechanism between the containing app and native extension. A loopback endpoint avoids granting a sandboxed extension access to arbitrary files in `~/.local/state` and keeps the Unix socket as the user-only CLI boundary.

The extension requests only two hosts: `chatgpt.com` and the exact loopback origin. It never requests `<all_urls>`, never extracts ChatGPT cookies/tokens, never calls private ChatGPT APIs, and does not submit prompts automatically. It opens `https://chatgpt.com/`, fills a narrowly identified composer when possible, and leaves the final Send action visible to the user.

## Pairing

1. Verify browser-pilot readiness without creating a pairing secret:
   `~/.local/share/chatgpt-conversation-harness-v1/bin/harness browser-status`
   This checks the loopback API and confirms that the staged extension directory contains a manifest.
2. Only after Safari has loaded the temporary extension, execute:
   `~/.local/share/chatgpt-conversation-harness-v1/bin/harness pair`
3. Copy the short-lived one-time code into the extension popup.
4. The extension receives a random bearer token. Only its SHA-256 hash is retained by the harness database. On later popup opens it reports whether the harness is reachable, whether the browser is paired, and how many handoffs are pending.

The browser token protects the loopback API from ordinary webpage access. The server also accepts requests only from WebExtension origins and binds exclusively to loopback. This is not a defence against malicious software already executing as the same macOS user; that remains outside the v1 threat boundary.

## Development packaging

The files in `safari/extension/` are standard WebExtension assets. Package them in a Safari Web Extension containing app with Xcode. The v1 browser path does not require `nativeMessaging`.
## Packaging for Safari

Xcode's `safari-web-extension-packager` can generate the containing macOS app without changing the harness or Shell Bridge runtimes:

```bash
safari/package.sh
```

Generated Xcode output goes to `safari/build/` by default and is intentionally ignored. Pass another directory as the first argument for an isolated test build. The script is non-interactive (`--no-open --no-prompt`), macOS-only, uses Swift, and copies the WebExtension resources into the generated project. Packaging does not install or enable the extension in Safari.

For rapid development, Safari can also load the raw extension folder temporarily through its Developer settings. The durable source of the extension remains `safari/extension/`; generated Xcode projects are build artefacts.

## Temporary installation without Xcode

For the macOS v1 pilot, Safari can load the WebExtension resources directly from disk. A normal harness install stages them at `~/.local/share/chatgpt-conversation-harness-v1/safari-extension/`. In Safari Settings, enable developer features, open the Developer pane, allow unsigned extensions if required, choose **Add Temporary Extension…**, and select that directory. This avoids the local Xcode/CoreDevice toolchain entirely. Temporary extensions are removed after 24 hours or when Safari quits; use the containing-app packaging path below for durable installation or distribution.
