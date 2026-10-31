# Live v6 client smoke — 2026-10-30

## Scope

Read-only end-to-end verification of the installed Local Executor Bridge v6 on the reference Mac, including authenticated raw Google Drive request upload, daemon execution, sandbox reporting and exact result readback. **This is not a complete release qualification or fresh-install test.**

## Observed installed build

- `bridge_version`: `6`;
- `build_code_integrity`: `matches_manifest`;
- `build_source_commit`: `a4286152f78615902a9b2a935d8bb423c940ddee`;
- request protocol: `1`.
- The installed build was verified independently of GitHub `main`. No upgrade was attempted.

## Reproduction method

1. Prepare a unique protocol-1 request with truthful `explanation`, known allowed `cwd`, and `write_scope: "read_only"`.
2. The first client-side `files.manage_library` raw upload attempt returned `container_session_expired` and **did not** submit a Mac request. A second attempt did not repair the session error.
3. A supported Drive connector file-reference route succeeded: a temporary private Google Doc containing only the JSON payload was exported to `text/plain`, producing a connector `file_uri`. The Drive upload action **required the string `file_uri.file_id`**, not the `file_uri` object suggested by its prose description. It accepted that exact connector file reference, uploaded raw `application/json` bytes, and returned a real file ID in the pinned `requests/` folder.
4. Google Docs were used only as an intermediate export source. The mailbox received a **raw JSON file**, not a native Google Doc. The export added a UTF-8 BOM; the v6 parser accepted its documented single-BOM compatibility format. This extra conversion is a client-specific workaround, **not the recommended standard installation workflow**. Prefer generating a raw file reference directly when supported.
5. Retrieve the exact matching `results/<id>.json` file and examine terminal status, exit code and sandbox fields; do not infer completion from upload acknowledgement.

## Verified result

| Property | Observation |
| --- | --- |
| Request ID | `bridge-v6-doc-export-smoke-20261030-cf71` |
| Result status | `completed` |
| Exit code | `0` |
| Duration | `0.093999` seconds |
| Filesystem sandbox | `true` |
| Network sandbox | `true` |
| Home read restriction | `true` |
| Requested/effective scope | `read_only` / `read_only` |
| `timed_out` | `false` |
| `output_limited` | `false` |
| `streams_incomplete` | `false` |
| Checkout HEAD | `a4286152f78615902a9b2a935d8bb423c940ddee` |

The test queried `pwd`, the checkout commit and `git status`; it did not write repository files. An existing untracked workspace directory was observed and left untouched.

## Residual risks / limits

- Only the read-only execution path is verified here. Repository mutation, coordinator lifecycle, system approval, rejection/expiry, crash-indeterminate recovery, and third-party fresh install are **not** established by this smoke.
- Client file upload capability is tool-specific; a text-only Drive API and a container file do not guarantee a raw mailbox upload.
- The first-party connector's input schema and descriptive prose disagreed over whether `file_uri` was an object or string. The tested interface accepted its `file_id` string. Do not generalise that shape to unrelated connectors.
- One temporary private Google Doc was created for the export. Deletion through the available Library surface was unsupported; clean it up using the Google Drive UI. Do not treat this scratch document as a deployed bridge artefact.
- Keep production upgrade and daemon-management work outside the mailbox, under [production operations](../shell_bridge/V6_PRODUCTION_OPERATIONS.md).

## Release gate

This evidence passes a client-to-v6 read-only smoke only. Publication of documentation can proceed after CI; a claim of *fully qualified public release* additionally requires third-party fresh-install, approval, sandbox, workspace and failure/recovery acceptance on supported macOS hosts.
