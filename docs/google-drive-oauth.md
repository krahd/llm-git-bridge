# Google Drive OAuth migration

Rclone's shared Google Drive client ID is being retired during 2026. `llm-git-bridge` should therefore use its own OAuth Desktop client while keeping the existing Drive mailbox and `root_folder_id` unchanged.

The migration deliberately installs **no Google software locally**. Do not install `gcloud` or Google Drive Desktop for this procedure. The only local dependencies are Python's standard library and the existing rclone installation.

## What can and cannot be automated

Google requires account/project consent decisions in its web UI. The helper opens the relevant Google Cloud pages, records the selected project ID, consumes the downloaded Desktop-client JSON, and reauthorizes only the configured bridge remote through a private temporary rclone config containing no unrelated remotes. It validates the candidate OAuth token against the existing mailbox before atomically updating the authoritative rclone config, refuses concurrent config edits, rolls back only when doing so cannot overwrite another writer, and restores the daemon to its previous running/stopped state.

The unavoidable browser-side actions are:

1. create or select a Google Cloud project;
2. enable Google Drive API;
3. configure Google Auth Platform Branding/Audience/Data Access;
4. create an OAuth client of type **Desktop app** and download its JSON;
5. approve the OAuth authorization when rclone reconnects.

For this bridge, keep the rclone scope as `drive`. The mailbox contains files created by both rclone and another OAuth client (the ChatGPT Google Drive connector), so `drive.file` is insufficient: it cannot generally see files created by other apps.

If a project in the same Google Workspace organisation as the Drive account offers an **Internal** audience, that is the simplest durable configuration. Otherwise use **External**, add the Drive account as a test user during setup, add `https://www.googleapis.com/auth/drive` under Data Access, and publish the app for durable use. External apps left in Testing receive refresh tokens that normally expire after seven days when non-basic scopes such as Drive are requested.

## Helper sequence

From the repository root:

```bash
python3 scripts/google_drive_oauth.py prepare
```

This opens Google Cloud project creation. After creating/selecting a project, copy its **project ID** (not merely the display name) and run:

```bash
python3 scripts/google_drive_oauth.py prepare --project-id YOUR_PROJECT_ID
```

The helper opens the Drive API and Google Auth pages for that exact project. Complete the browser actions listed above and download the Desktop-client `client_secret_*.json` to `~/Downloads`. Then run:

```bash
python3 scripts/google_drive_oauth.py finish
```

`finish` automatically chooses the newest recent valid Desktop credential JSON belonging to the project recorded by `prepare`. To select a file explicitly instead:

```bash
python3 scripts/google_drive_oauth.py finish --credentials /path/to/client_secret.json
```

During `finish`, rclone opens the Google authorization page. Sign into/authorize the same Google Drive account used by the bridge mailbox. The client secret is passed to `rclone obscure -` over stdin and is never placed on a command line; only rclone's obscured representation is written to configuration. Before the authoritative config is changed, the candidate token must list the mailbox and complete a create/read-back/delete probe inside `v2/meta`, so a read-only or incorrectly scoped token is rejected safely. The helper also verifies that the authoritative rclone config has not changed since it was read and uses guarded rollback if a later step fails. On success it restarts the bridge and deletes the downloaded credential JSON. Use `--keep-credentials` only if you intentionally want to retain that file.

Afterward, the remote `doctor` request should report `custom_drive_client_id_configured: true` and a healthy RC socket.
