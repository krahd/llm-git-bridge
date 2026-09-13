# Performance

This document separates mailbox overhead from repository validation time and records the measured performance work completed in September 2026.

## Reference host

The production measurements below were taken on one macOS host. The post-promotion doctor reported:

- Python 3.14.7;
- Apple Git 2.50.1;
- rclone 1.75.1;
- persistent rclone RC enabled and healthy.

Treat these numbers as reference measurements, not an SLA or cross-platform benchmark.

## Validation-suite improvement

A strict audit found that the same test suite that took roughly nine seconds in an isolated Linux audit environment took several minutes on the production Mac. Controlled probes showed unusually high per-process cost on that host, while the test suite launched more than a thousand Git subprocesses.

The remediation retained validation coverage while collapsing repeated Git/process work.

| Stage | Live full test command | Test count |
|---|---:|---:|
| Pre-remediation | 373.2 s | 139 |
| First remediation pass | 106.3 s | >=139 |
| Second remediation pass | 73.2 s | 143 |
| Final remediation pass | **55.8 s** | **147** |

From the original live baseline to the final pass, that is approximately **6.7x faster** and **85% lower wall-clock time**.

Major changes included:

- one porcelain-v2 `git status` call for repository state;
- consolidated branch/ref and commit metadata lookups;
- one NUL-safe raw staged diff for path and mode validation;
- tracked-file command workspaces rather than copying unrelated untracked trees;
- fewer redundant worktree cleanup and patch-validation Git invocations;
- reuse of already-known Git object IDs/control state;
- lower-process-count test fixtures;
- rclone-RC health checks tolerant of a single transient liveness miss.

The security and stale-state checks were not removed to obtain the speedup.

## Control-plane latency after promotion

Immediately after the final code was promoted and the daemon restarted, live requests used the persistent rclone-RC path.

Post-promotion `doctor`:

- transaction-directory list: **0.227 s**;
- request download: **0.442 s**;
- processing before result upload: **0.972 s**;
- complete daemon request lifecycle, including result upload and request cleanup: **3.675 s**.

Post-promotion production materialisation request:

- transaction-directory list: **0.227 s**;
- request download: **0.444 s**;
- processing before result upload: **6.749 s**;
- complete daemon request lifecycle: **9.461 s**.

Materialisation includes building and publishing a repository snapshot, so it is intentionally more expensive than a small control request.

A five-request control-plane concurrency probe then completed all five requests successfully. Four successful request lifecycles were around **3.61-3.95 s**. One request experienced a **4.75 s** RC download and took **10.90 s** for its successful lifecycle; another request was deferred twice after transient 13-second download attempts before later succeeding in **3.66 s**. This illustrates why mailbox timings are variable network/storage latency rather than a fixed constant, and why dependent clients must wait for signed results instead of assuming submission order.

## What dominates an edit transaction now?

For the bridge repository's final optimisation transaction:

- configured `test` command: **55.8 s**;
- total local transaction work: **61.5 s**.

In other words, once transport is healthy, a serious edit transaction is dominated by the repository's own validation command. The bridge's local patch/worktree/commit/push overhead is much smaller than the test suite.

## Native GitHub integration comparison

A native GitHub integration and `llm-git-bridge` solve overlapping but not identical problems.

A direct GitHub API/plugin path normally avoids:

- Drive mailbox polling;
- Drive request/result uploads;
- the local watcher queue;
- local snapshot materialisation before repository reads.

For repository browsing and metadata operations, direct GitHub access should therefore have a structural latency advantage. GitHub's API is designed for direct authenticated requests and supports thousands of authenticated requests per hour, subject to primary and secondary rate limits.

The bridge deliberately adds work a native GitHub read path does not provide: it operates on the local authoritative checkout, validates an exact base, applies patches in isolated worktrees, runs locally configured tests/builds, and can keep GitHub credentials entirely out of the remote LLM protocol.

There is no published latency SLA for ChatGPT's GitHub plugin that would support an honest universal ratio such as "the plugin is 3x faster". The meaningful comparison is therefore workload-specific:

- **read/search GitHub repository:** native integration is expected to be faster and simpler;
- **small GitHub write with no local validation:** native integration is expected to be faster;
- **edit that must run local tests/tools against your checkout:** the bridge's extra latency buys that local validation boundary;
- **many simultaneous sessions:** a native service can handle concurrent API requests, while this bridge intentionally serialises local execution.

GitHub itself recommends efficient authenticated API use, avoiding unnecessary polling, and serialising large numbers of mutating requests to reduce secondary-rate-limit risk.

## Remaining transport optimisation

The bridge supports migration to a user-owned Google OAuth Desktop client. On the current reference installation, the post-promotion doctor still reports `custom_drive_client_id_configured: false`.

That does not make the bridge non-functional—the RC path is currently healthy—but a private OAuth client remains recommended for sustained use and isolates the installation from rclone's shared-client quota/policy. See [google-drive-oauth.md](google-drive-oauth.md).

## External references

- GitHub REST API rate limits: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- GitHub REST API best practices: https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- rclone Drive backend documentation: https://rclone.org/drive/
