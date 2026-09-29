# Safari re-entry prototype

This is the browser half of Conversation Harness v1. It is deliberately an **actuator, not an authority**.

The extension uses a narrow authenticated loopback API on `127.0.0.1:47653`. Earlier architecture drafts proposed Safari native messaging directly to the harness Unix socket. That was rejected after adversarial review because Safari's native app extension is sandboxed; Apple documents app groups as the supported data-sharing mechanism between the containing app and native extension. A loopback endpoint avoids granting a sandboxed extension access to arbitrary files in `~/.local/state` and keeps the Unix socket as the user-only CLI boundary.

The extension requests only two hosts: `chatgpt.com` and the exact loopback origin. It never requests `<all_urls>`, never extracts ChatGPT cookies/tokens, never calls private ChatGPT APIs, and does not submit prompts automatically. It opens `https://chatgpt.com/`, fills a narrowly identified composer when possible, and leaves the final Send action visible to the user.

## Pairing

1. With the parallel harness daemon running, execute:
   `~/.local/share/chatgpt-conversation-harness-v1/bin/harness pair`
2. Copy the short-lived one-time code into the extension popup.
3. The extension receives a random bearer token. Only its SHA-256 hash is retained by the harness database.

The browser token protects the loopback API from ordinary webpage access. The server also accepts requests only from WebExtension origins and binds exclusively to loopback. This is not a defence against malicious software already executing as the same macOS user; that remains outside the v1 threat boundary.

## Development packaging

The files in `safari/extension/` are standard WebExtension assets. Package them in a Safari Web Extension containing app with Xcode. The v1 browser path does not require `nativeMessaging`.
