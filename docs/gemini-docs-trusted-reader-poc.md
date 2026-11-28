# Gemini Google Docs transport: pinned Drive reader pilot

Status: **inert prototype for review; not installed, enrolled, or enabled in production**. Issue: [#94](https://github.com/krahd/llm-git-bridge/issues/94).

## Evidence and decision

The user authenticated \`gemini-pedeciba:\` in rclone, and an interactive Mac Terminal listed three PEDECIBA Drive test files. Gemini's indexed Workspace search correctly read an externally edited Doc, an externally created Doc, and a CU-owned Doc shared with the PEDECIBA identity. Search indexing has not been qualified for freshness or repeatability.

The exact v6 mailbox diagnostic \`gemini-bridge-sandbox-probe-20261127-a1\` **ran once and finished with exit code 1**. Its stderr reported that the read-only sandbox could not find the rclone config or the \`gemini-pedeciba\` remote. This is an expected failure of credential isolation, not grounds to weaken it. Do not replay this request ID.

The user also reports that Gemini uploaded a ZIP file directly to Google Drive. This establishes that at least one Gemini workflow can upload a generated file; it does **not** yet establish that the same workflow can upload a raw JSON file with a stable filename to an exact target folder. Test that narrower capability before investing in a permanent Google Docs transport.

## Prototype boundary

\`gemini_drive_read_helper.py\` is a **direct-exec trusted-operation candidate**, not a general shell, a second daemon, or an install script. It takes exactly \`ACTION POLICY_DIRECTORY\` and supports:

- \`probe-test\`: retrieve *only* the fixed test Doc \`bridge-external-result-test.txt\` from \`gemini-pedeciba:\`.
- \`inspect-requests\`: list at most 12 root-level \`gemini-bridge-request-*.txt\` documents and return only strictly validated inert \`diagnostic_echo\` JSON requests. No actions or commands inside documents are executed.

It pins the remote, rclone executable SHA-256, explicit private rclone config file, exact document namespace, output byte caps and a 20-second subprocess timeout. It suppresses rclone stderr in remote responses, never returns OAuth tokens, and rejects extra fields, duplicate JSON keys, malformed names, unsafe file permissions and modified binaries. These safeguards are **local prototype invariants** and need adversarial review before live enrollment.

All remote actions operate against the operator's **read-only PEDECIBA Drive OAuth grant**. The production v6 sandbox remains unchanged. A trusted helper runs with extra authority, so the installed executable and its operator-owned configuration must be outside \`/Users/tom/tom-repos\` and must be registered by the operator with the existing v6 trusted-operation mechanism. Every invocation requires native local approval.

## Offline verification

From the exact reviewed source checkout:

\`\`\`sh
python3 -m unittest shell_bridge.tests.test_gemini_drive_read_helper -v
\`\`\`

Do not mistake passing unit tests for end-to-end proof of Gmail/Drive access or Gemini search-index freshness.

## Operator-only enrollment, if approved

1. In an interactive Terminal, identify \`rclone config file\` and the resolved, non-symlink rclone executable (\`realpath "$(command -v rclone)"\`). Do not print/share the rclone config's contents, which can contain OAuth tokens. Confirm the rclone config is operator-owned and mode \`0600\`.
2. Create a new operator-owned private policy directory **outside the agent-writable root** (mode \`0700\`) with a \`drive-policy.json\` file (mode \`0600\`) of precisely this structure, using the *real* local paths and SHA-256 of the resolved rclone executable:

   \`\`\`json
   {
     "schema": 1,
     "remote": "gemini-pedeciba:",
     "rclone_binary": "/ABSOLUTE/REAL/PATH/TO/rclone",
     "rclone_sha256": "<64 lowercase hex characters>",
     "rclone_config": "/ABSOLUTE/PATH/TO/rclone.conf"
   }
   \`\`\`

3. After independent review, first run the helper **directly and locally** with \`probe-test\` and that policy directory. Verify only the intended test Doc content appears. Do not grant arbitrary file-read or writable Drive scopes to make a failing test pass.
4. A production trusted operation requires an immutable operator-installed, executable copy of the reviewed helper **outside** the writable root and its exact SHA-256 in \`trusted_operations\`, with \`permitted_roots\` containing **only the private policy directory**, and \`permitted_actions\` containing only \`probe-test\` and \`inspect-requests\`. Do not edit the running release's config or restart the daemon in-band. All activation/cutover must follow [v6 production operations](../shell_bridge/V6_PRODUCTION_OPERATIONS.md) from an interactive operator Terminal, preserving the one-daemon/mailbox/journal invariant.
5. Use a new unique v6 request ID for the *trusted* \`probe-test\` action, without a shell \`command\` field, and accept the local approval only after reviewing the exact helper hash and policy root. Validate the matching result and request SHA; stop on any unknown or indeterminate state.

## Go/no-go before further development

A live, locally approved \`probe-test\` must return the known test result. Then a Gemini-created request must be found by \`inspect-requests\`; a separate CU-owned result Doc must be created and found by Gemini. Run serial, duplicate and stale-index cases. No code should publish arbitrary shell commands from Gemini documents. A permanent transport is justified only if the full round trip is dependable and meaningfully simpler than direct ZIP/raw-JSON upload.
