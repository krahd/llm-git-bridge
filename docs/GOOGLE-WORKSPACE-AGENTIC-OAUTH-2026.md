# Google Workspace agentic OAuth client classification (October–November 2026)

**Decision status:** administrator action required; **no live OAuth configuration changed**. This is not an instruction to stop any daemon or replace the current mailbox credentials.

## What changed

Google Workspace notified the project administrators that Google OAuth clients used by AI agents may be marked `is_agentic` (in the Google Cloud console, **Google Auth Platform → Clients → Use this client for an AI-powered agent**) from **12 October 2026**. According to the notice received on 12 October:

| Date | Reported effect |
| --- | --- |
| 12 October 2026 | Agentic classification available on existing OAuth clients. |
| 11 November 2026 | Google's **Workspace MCP** servers require an agentic OAuth client. |
| 12 November 2026 | Existing client classification becomes immutable; changing classification subsequently requires a new OAuth Client ID. |

**This is not a blanket 11 November shutdown of Drive REST API access.** The bridge transport currently uses `rclone` and the Google Drive REST API, not Google's hosted Workspace MCP service. Google says REST-only integrations that are **not** agents are unaffected. Our bridge, however, processes AI-agent requests and therefore falls under the *AI-agent workflow* classification guidance even while using REST.

Google's documentation: [Manage OAuth Clients](https://support.google.com/cloud/answer/15549257?hl=en), including **Configure OAuth Clients for AI Agents**. [rclone Drive configuration](https://rclone.org/drive/) documents private OAuth client IDs and the retirement of rclone's shared client during 2026. These are **two independent changes**. The Google agentic flag does not repair a deprecated shared rclone client.

## Classification for this repository

**Classify a dedicated bridge-owned OAuth client as agentic.** It authorizes an AI agent's automated Drive mailbox retrieval/publication. If the same Google OAuth client ID is shared by unrelated human-driven or conventional REST applications, **do not reclassify it unilaterally**: Google's guidance is to use separate client IDs for agent and non-agent traffic so Workspace admins can apply independent agent access controls.

Whether an existing client ID for the `llm-git-bridge` Google Cloud project is actually the credential used by the running `chatgpt-git-bridge:` rclone remote is **not established** by the email. Do not assume the emailed Cloud project name proves the active remote uses that project's client ID. The installer already emits a warning if a redacted rclone configuration lacks a private `client_id`, because the shared rclone OAuth client is being retired. Resolve that separate exposure before depending on the new classification.

## Safe administrator procedure (no runtime bridge mutation yet)

1. In the Google Cloud console, select project **`llm-git-bridge`**, open **Google Auth Platform → Clients**, and identify each actual OAuth Client ID used by this project. Do **not** post IDs, secrets or refresh tokens in GitHub issues or request journals.
2. Independently establish which client ID the **currently configured** Mac rclone Drive remote actually uses (without revealing tokens). Check whether that client is dedicated to the bridge or shared with non-agent applications. If it uses rclone's retired shared client, the project may not own that client's classification and should plan a **new private client**.
3. For a dedicated, bridge-owned agent client: mark **Use this client for an AI-powered agent** before **11 November 2026** if hosted Workspace MCP access may be needed. Since the setting becomes permanent on **12 November**, choose carefully. For a mixed client, create a **separate agentic client** instead; keep the conventional client separate.
4. Review the Workspace administrator's agent access controls and the OAuth scopes/consent configuration. The flag does **not** add Drive permissions, replace user consent, or grant new API scopes.
5. If credentials must change, prepare and validate an **independent new rclone remote** outside the live bridge. Verify that it can access the *same pinned* Drive root and request/results folder identities with the intended least-privilege access. Do not overwrite the running `chatgpt-git-bridge:` remote, reauthorize the active token, rotate its client ID in place, change folder IDs, or switch the daemon while ownership and journal state are unresolved.
6. At the later guarded cutover, migrate the transport credential only with an explicit before/after identity check, operator approval where necessary, and a proven rollback. Never let two active consumers poll the same request mailbox as a side effect of OAuth testing.

## Release tracking and failure semantics

- **No service stop, restart, OAuth client edit, token exchange, Drive mailbox command, or stored credential change is authorised by this runbook.**
- The existing Mac owner conflict, the unresolved historical `JOURNAL_HANDOVER_HOLD`, and approval/recovery qualification remain independent blockers for the v6 migration. A successful OAuth configuration change would **not** clear them.
- Do not label OAuth classification complete until the real client ownership, traffic separation and administrator setting have been verified in Google Cloud, and any necessary rclone credential transition has independently passed access and rollback checks.
- If the bridge continues using Drive REST only, **11 November is not an outage deadline**. The November date is material for adopting Google's Workspace MCP servers; the new client's dedicated-agent classification and the separate rclone shared-client retirement still need deliberate handling.

**Repository owner action:** complete the Cloud Console client inventory/agentic decision by **11 November 2026**. If there is uncertainty about an existing mixed-use client, prefer creating a dedicated agentic OAuth Client ID rather than irreversibly changing a shared one.
