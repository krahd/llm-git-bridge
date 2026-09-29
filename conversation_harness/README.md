# Conversation Harness v1

Conversation Harness v1 is an **independent companion service** for durable conversational work. It may coexist indefinitely with ChatGPT Shell Bridge v5. It does not replace, restart, configure, or share runtime state with the shell bridge.

Runtime namespace:

```text
~/.local/share/chatgpt-conversation-harness-v1/
~/.local/state/chatgpt-conversation-harness-v1/
~/Library/LaunchAgents/net.laurenzo.chatgpt-conversation-harness-v1.plist
```

The harness owns workflow state: jobs, fenced leases, handoffs, continuation projections, external-operation identities, and an append-only event history. GitHub remains canonical repository state; `workspace.py` remains the Git mutation coordinator; Google Drive remains transport for ordinary ChatGPT conversations.

## Stage without activating

This is the default and is safe while Shell Bridge v5 is in use:

```bash
bash conversation_harness/install.sh --stage-only
```

This copies the isolated runtime and writes its own LaunchAgent plist, but does not call `launchctl`.

## Activate the parallel harness

After tests and review:

```bash
bash conversation_harness/install.sh --activate
```

Activation touches only `net.laurenzo.chatgpt-conversation-harness-v1`. It does not unload or restart any Shell Bridge label.

## Temporary Safari pilot (no Xcode required)

Current Safari can load a WebExtension folder temporarily for development, so the v1 browser pilot does not depend on a working Xcode installation. The installer stages the canonical extension source at:

```text
~/.local/share/chatgpt-conversation-harness-v1/safari-extension/
```

In Safari, enable **Show features for web developers**, open **Settings > Developer**, allow unsigned extensions if prompted, choose **Add Temporary Extension…**, and select that staged folder. Safari removes temporary extensions after 24 hours or when Safari quits, so this is a development/pilot path rather than distribution.

After loading the extension, first verify that the loopback API and staged extension match the installed manifest:

```bash
~/.local/share/chatgpt-conversation-harness-v1/bin/harness browser-status
```

Only when that reports `"ready": true`, create the short-lived pairing secret:

```bash
~/.local/share/chatgpt-conversation-harness-v1/bin/harness pair
```

Enter the one-time code in the extension popup. The extension receives only the scoped loopback bearer token and still leaves the final ChatGPT Send action to the user. Xcode packaging remains the route for a durable/distributable Safari containing app, but is not required for the v1 acceptance pilot.

## Local protocol

The daemon listens on a user-only Unix socket:

```text
~/.local/state/chatgpt-conversation-harness-v1/harness.sock
```

Messages are one JSON object per line. Protocol version 1 requires a unique `request_id` for mutations. The daemon journals mutating requests before execution; if it crashes after a request starts but before it can durably record the response, replay returns `indeterminate_request` rather than silently repeating the mutation.

Smoke test:

```bash
~/.local/share/chatgpt-conversation-harness-v1/bin/harness call \
  '{"protocol":1,"action":"ping","args":{}}'
```
