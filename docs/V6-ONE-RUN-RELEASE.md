# v5 to v6: one-invocation guarded local release

## Scope

The supported operator entrypoint is `shell_bridge/v6_release_once.sh`. Run it
from a **clean, isolated worktree** at the exact audited canonical release
commit. Do not run `git pull` in a dirty local checkout, and do not install over
the existing v5 binary/config.

This is one local invocation, not an unattended or unconditionally successful
cutover. No finite test suite can guarantee that external Google Drive,
LaunchAgents, or the native approval GUI will behave identically on the user's
Mac. The entrypoint holds on real uncertainty instead of coercing migration.

## Historical indeterminate requests block mailbox handover

A request whose execution is marked FINISHED can still have status
`indeterminate`: the bridge may have terminated a child during shutdown,
lost output to surviving descendants, or been unable to prove its effect.
Consequently, counting only `started_without_finished=0` is insufficient
to authorize moving production ownership.

v6 health now separately reports `indeterminate` and
`unverifiable_finished` journal counters. Before stopping the original
consumer and again after that shutdown, the cutover controller independently
inspects the actual local durable journal using
`verify_quiescent_journal.py`. Any STARTED-without-FINISHED record,
FINISHED-indeterminate record, malformed marker, dangling symlink or
uninspectable journal holds the cutover. The verifier does not modify or
discard past execution evidence. Manual reconciliation is required even
when Drive has no pending requests and an old heartbeat appears quiescent.

## Shutdown ownership and incomplete pipe results

The bridge holds all mailbox flock handles until its request worker threads
have joined on graceful daemon shutdown. The former five-second join timeout
could release the exclusive owner lock while a worker remained active or was
still persisting its result. Forced process termination remains a separate
OS/journal-recovery concern; this change does not qualify a `kill -9` or
uninspected service bootout as safe.

If a spawned subprocess exits but stdout/stderr/stdin reader/writer threads
cannot fully drain within the bounded collection window, the request is
recorded as **indeterminate**, not successfully completed. A detached child
may inherit the parent's pipes and survive the original process exit. The
bridge must not call that a verified completed request or replay its effects
without journal reconciliation.

## Duplicate Drive result evidence is a release hold

Google Drive permits multiple files with the same name. The live staging
mailbox yielded two independently persisted result records for one request ID,
with matching request hashes but different durations. The reason for the
duplicate generation has not been reconciled; never infer exclusive execution
from one successful result fetch.

The release controller now independently lists the exact canary result name
and demands **exactly one existing Drive file ID** for each staging/production
native approval, pinned SSH status and production smoke. Zero, multiple, or
ambiguous results fail closed before acceptance. This is a diagnostic
cardinality gate, **not** an exactly-once guarantee: another producer could
still publish after the check, and asynchronous/indeterminate journal states
remain governed by their own reconciliation requirements. Do not operate
two loaded bridge services against the same mailbox merely because this gate
has passed once.

## Per-user mailbox locking and LaunchAgent environments

Previous installed bridge versions derive the mailbox lock path beneath the
process-specific `TMPDIR`. Two processes in different launch environments
can therefore derive different OS lock inodes for the **same** Drive mailbox
identity. Matching mailbox IDs is not proof that their `flock` operations
contend.

When a v6 config contains both a canonical Drive folder ID and the
operator-pinned remote/base folder name, newer builds also acquire the
named-mailbox alias lock. This prevents a concurrent new v6 daemon configured
by legacy remote/base naming alone from bypassing the canonical-ID lock.
A missing or malformed canonical Drive token must not silently become a
different lock namespace. The alias supplements, rather than replaces,
the pinned ID; older binaries with unrelated TMPDIRs still require a
loaded-owner handover check.

Newly installed v6 builds now hold both the historical TMPDIR lock and
an additional per-user fixed `/tmp/chatgpt-shell-bridge-<uid>/` lock.
The fixed lock is owner-only and rejects symlinked/non-regular lock files.
It remains held until shutdown. Acquiring *both* locks preserves exclusion
against old binaries that use the same TMPDIR and against new binaries
using different TMPDIR environments. Unit tests include real separate
processes with different TMPDIR roots.

This change **cannot protect against an older binary in another TMPDIR**;
the local LaunchAgent inventory and running-owner acceptance gates must
therefore still refuse production activation while older mailbox claimants
are loaded. The OS lock observer reports both known lock locations and never
infers retirement readiness from open descriptors. Do not apply any
unqualified in-place replacement to a live daemon.

## Non-disruptive instance-lock descriptor evidence

`shell_bridge/audit_runtime_processes.sh` additionally invokes
`bridge_lock_observer.py` to inspect open file descriptors for the
mailbox-scoped locks used by bridge v6. It derives lock paths using the
same per-user temporary directory and SHA-256 mailbox identity scheme
as the running bridge, without calling the lock-acquisition routine or
creating/changing lock files. It returns candidate PIDs and explicitly
marks whether `lsof` could inspect an existing lock path.

**An open descriptor is not proof of an exclusive `flock` owner.**
Absence of an observed descriptor is not proof that an older bridge,
different version, or restartable LaunchAgent cannot consume that mailbox.
The observer never authorizes shutdown, deletion, GC, rollback or v5
retirement. If descriptor inspection fails, treat it as unknown and
preserve the fail-closed LaunchAgent ownership gate.

This is a local diagnostic invoked only where Mac access is available;
passing the macOS-hosted CI regression suite is not live-user-Mac
acceptance.

## Read-only reconciliation of an already-v6 heartbeat

From an exact, current canonical source worktree on the Mac, run
`bash shell_bridge/audit_runtime_processes.sh` to record a bounded
inventory of loaded legacy and v6 LaunchAgents, executable identities,
approval-app presence, and the existing verified mailbox-owner report.
The helper performs no service changes or deletions, omits command
arguments and caps emitted process records. A green CI test of this helper
on a hosted Mac is **not** evidence about the operator's actual Mac;
that read-only inventory must still be collected in the local user session.

Do not start the v5-first-cutover controller when the production heartbeat
declares v6; first identify which loaded LaunchAgent, install manifest and
durable journal produced that heartbeat, and then determine whether the
correct procedure is a staged v6 upgrade or final live acceptance.

## Live production identity hold (2026-10-11)

Read-only access to the pinned production Drive root returned a fresh
`health.json` at `2026-10-12T00:57:45Z`. Contrary to the historical
assumption that the mailbox was reporting v5, that heartbeat declares
`bridge_version: "6"` and `build_source_commit: "0b84b2f..."`,
with no active requests and no STARTED-without-FINISHED work. Its
instance ID still uses the historical production bridge identity.

**This heartbeat does not prove which LaunchAgent owns the mailbox.**
Another installed version may have published it, or the existing service
may have been updated in place. Both possibilities require local inventory
before any further migration or retirement. Do not erase the older release
worktree, stop a consumer, or blindly re-run the one-paste cutover while
this discrepancy is unresolved.

The release entrypoint now verifies a fresh **v5** heartbeat with the
expected instance and root before staging. `cutover.sh` independently
rechecks the same identity immediately before stopping any service. These
checks intentionally HOLD on observed v6/unknown production status:
they protect against mistakenly retiring an unrecognized or already
migrated bridge. The live Mac LaunchAgent/process inventory, installed
manifest hashes, and durable journals remain authoritative for final
reconciliation. After a true already-v6 installation is established,
use an explicit v6-to-v6 upgrade or acceptance procedure rather than
misrepresenting it as a v5-to-v6 first cutover.

## Gates in execution order

1. Validate the clean Git snapshot, run all Python and shell tests, inventory
   every loaded bridge-related LaunchAgent and its actual pinned mailbox, and
   refuse unknown/multiple live owners.
2. Install and run a uniquely named isolated candidate in its own Drive mailbox;
   verify exact source SHA, manifest integrity, live launchd PID, fresh heartbeat,
   and a hash-bound read-only result under the expected sandbox.
3. Require **two actual native approval actions** on a SHA-pinned, read-only
   helper: first **Deny**, then **Approve**. Timeout, implicit rejection, GUI
   failure, approval replay, and mismatched request hashes cannot qualify.
4. Stage the same source commit for the existing v5 production mailbox **without
   starting it**. Pin the existing production root, requests/results IDs, allowed
   root, and preserved v5 state directory.
5. Present one explicit **MIGRATE** confirmation immediately before stopping v5.
   `cutover.sh` rechecks fresh quiescence, pending approvals, unresolved STARTED
   work, mailbox emptiness, and all real loaded production consumers. It stops
   only recognized consumers of the target production mailbox and starts v6.
6. Require hash-bound production read-only smoke and an additional native
   production approval before accepting the provisional handover.
7. Re-audit loaded LaunchAgents after cutover and require exactly one owner\n   of the production mailbox; any reloaded legacy or unknown consumer holds.\n8. Archive only the production v5 LaunchAgent plists that were actually stopped,
   after verifying v6 remains loaded. Keep v5 journals, configs, Git worktrees,
   rollback evidence and all unrelated staging services intact. The archive
   prevents those specific legacy production plists being auto-loaded at the
   next user login.

The produced `v6-release-acceptance.json` records both what passed and what
remains unverified. **Live crash/replay fault injection is not asserted** merely
because regular CI and normal operation passed. A successful one-run
handover archives the stopped v5 LaunchAgent plists **reversibly** to prevent
both versions from claiming the same mailbox after the next login. The
acceptance level is `provisional_handover`, with
`recovery_fault_injection_live_verified=false` and
`v5_sunset_ready=false`. Do not delete the v5 installation, journals, or
rollback evidence based on that provisional record; independent recovery
and client tests must precede permanent retirement.

## Failure handling

- Before stopping v5: a failure leaves production untouched; a partially
  created isolated candidate or staged production installation may remain.
  Reconcile it before retrying; never delete the old state or blindly replay a
  request with a new ID.
- After stopping v5 but before v6 starts: the cutover script attempts to
  restore exactly the previously loaded consumers. If restoration is incomplete,
  preserve its reconciliation marker.
- Once v6 may have admitted a request: an ambiguous failure **holds both
  consumers** and records manual journal/mailbox reconciliation instructions.
  It must not automatically restart v5, which could duplicate external effects.
- If verification after a provisional production switch fails, retain v6 and
  all rollback evidence. Inspect reality before a later action; no blind
  rollback or service retirement.
- The acceptance/archival step is reversible, not deletion. A partial archive
  is an explicit hold requiring reconciliation before another attempt.
- Other old staging LaunchAgents remain loaded until their own requests,
  approvals and journals are inspected. Do not retire unrelated provider
  sessions based solely on historical label names.

## Recovery after the 81accc8 interrupted launcher

The October 10 attempt reached outbound SSH enrollment and created a
detached worktree at
`~/.local/state/bridge-v6-release-sources/81accc8e365a54d95c11c0d606527e338c8515cc`.
It also exposed a heredoc parser defect: Bash included a continuation
`hold` statement in the Python source. The resulting `IndentationError`
did not stop worktree creation. The corrected launcher uses a complete
`if ! python3 ... <<'PYSAFEPATH'` guard with the closing delimiter before
`then`, and regression checks that an unsafe path cannot advance.

Do **not** rerun that exact source SHA or remove its worktree blindly.
After successful qualification of a newer canonical main, the launcher
creates a different SHA-keyed worktree. Verify the old worktree and any
partial candidate state before cleanup. The displayed prompt was still
pre-staging, but a runtime-side inventory is required to establish that v5
continues to own production.

## Operational preconditions

Requires a macOS user session with `git`, `python3`, `rclone`, `launchctl`
and the full macOS Swift SDK. The existing v5 config and the established
`chatgpt-git-bridge:` rclone remote must remain accessible. The production
root is currently pinned to `1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6` to prevent
accidental adoption of an unrelated mailbox.

Three native GUI decisions are required (Deny, Approve, Approve), plus one
terminal confirmation for the consequential production switch. All other
ordinary staging and testing actions proceed without repeated operator
confirmations. **Do not run this script through the production bridge
it intends to stop.**

After the GitHub PR has been integrated and a canonical commit independently
verified, use a **single terminal paste** that fetches that SHA into a distinct
release worktree and invokes `bash shell_bridge/v6_release_once.sh`.

Do not claim full ecosystem migration or retire other staging instances until
their independent mailbox/journal ownership and provider routing are reconciled.

## Optional outbound SSH enrollment

Outbound SSH is a separately enrolled bridge capability, **not** a prerequisite
for Drive-backed command transport. The default is
`V6_ENABLE_OUTBOUND_SSH=0`: no SSH host is requested, no pinned helper
is registered and no SSH status probes are submitted. The release records
`OUTBOUND_SSH_NOT_REQUESTED=1` and marks the two SSH probes skipped.

To include pinned outbound SSH acceptance, set
`V6_ENABLE_OUTBOUND_SSH=1`. Supply a pre-reviewed
`V6_SSH_POLICY_DIR`, or complete interactive, independently verified
host-key enrollment. The installer pins the operator-owned policy,
known-host database and SSH identity and registers the bounded helper.
Both isolated-staging and production SSH status canaries then become mandatory
and fail closed. A supplied policy without the explicit opt-in is rejected;
values other than `0` or `1` are rejected.

Skipping SSH is not proof of feature parity. Before stopping v5, the launcher
still requires `MIGRATE LIMITED SSH` to acknowledge that arbitrary SSH,
SCP, SFTP and rsync have not been qualified. Do not consent to migration
if current workflows depend on those capabilities.

## Single-run interactive SSH enrollment

When outbound SSH is explicitly enabled and no policy directory is supplied,
`setup_v6_pinned_ssh.py` prompts for the pinned hostname, account, port,
existing owner-only identity and previously trusted `known_hosts` file. It
requires a verified fingerprint and explicit `PIN SSH` confirmation, never
uses trust-on-first-use, and refuses to overwrite an existing enrollment.

## Additional operator-pinned SSH profiles

Schema-2 SSH policies can enroll fixed, operator-reviewed remote argv profiles
alongside the two built-in read-only probes. The policy lives outside the
agent-writable repository root and is pinned at installation. Unlike the
built-in status and identity probes, **each custom profile action requires
a native consequential approval** and an unchanged pinned host, policy file,
known-host database and identity key. The provider cannot alter the host,
command or arguments in its request.

The one-run enrollment wizard creates only the minimal built-in policy.
Additional profiles must be configured and reviewed by the operator before
installation. Custom profiles extend bounded remote maintenance, but they do
not qualify arbitrary SSH commands or SCP/SFTP/rsync. Keep the explicit limited
SSH consent and the v5 feature-parity hold until required workflows are proven
on the live Mac.

## Deliberate limited-SSH release decision

Passing the pinned `status` probe does not imply general SSH/SCP/SFTP/RSYNC
support. Before stopping v5, the release controller states this limitation and
requires the explicit one-time phrase `MIGRATE LIMITED SSH` to accept it.
If previous production workflows depend on arbitrary outbound SSH or transfers,
**do not authorize the cutover**; keep v5 running until corresponding bounded
SSH capabilities and live tests are available. This is a business-functionality
and safety gate, not a request to weaken the sandbox.

## Optional inbound SSH acceptance

Direct macOS Remote Login is separate from the Google Drive bridge transport
and is **not required** to operate v6 remotely through the bridge. By default,
the one-run controller records `INBOUND_SSH_NOT_REQUESTED=1` and skips
Remote Login inspection and external-device witness verification.

To qualify direct inbound SSH as an additional capability during the same
release, run the launcher with `V6_REQUIRE_INBOUND_SSH=1`. In that mode,
both the restricted-account preflight and real separate-device witness
remain mandatory and fail closed. Other values are not an enablement;
only the exact value `1` opts in. This opt-in does not configure or expose
Remote Login or qualify reachability over the public internet.

## Inbound SSH requirement and limits

Inbound SSH is macOS Remote Login, not the bridge transport. When `V6_REQUIRE_INBOUND_SSH=1` is selected, the one-run controller checks
Remote Login status and restricted account access before production staging. If this
is not yet configured, the operator can use macOS System Settings > General >
Sharing > Remote Login, restrict access to specific authorized users, and
recheck from the same running script. No script enables Remote Login, installs
a service, opens a firewall port or weakens host authentication.

With inbound SSH acceptance enabled, a fresh 48-hex-digit challenge is
generated in a private local state directory before production switching. An operator must establish a genuine SSH login
to the Mac **from a separate device**, execute the displayed witness command
in that authenticated session, and let the running controller verify its
identity, peer address and freshness. A missing or expired witness blocks
production handover only when inbound SSH acceptance was explicitly requested.
This is a separate-client SSH session diagnostic; it does not independently
establish public-internet routing, nor can local source
tests prove access through an off-site firewall/VPN.

The SSH login account can operate through ordinary macOS SSH authorization;
it does not gain additional arbitrary command authority inside the AI bridge.
The existing outbound pinned SSH helper still permits only previously
approved fixed read-only actions without repeated bridge prompts.

The operator should use key-based SSH authentication, restrict Remote Login
to necessary users and private/VPN network paths where practicable, and verify
host keys independently. Remote Login should never be automatically exposed to
the public internet by a migration script.

## Latest source audit findings

A fresh pass identified two broken release canaries that prior tests missed:
native approval omitted the bridge-required durable operation ID, and outbound
SSH smoke generated a request ID with a literal trailing dollar sign.
Both were corrected and tested against the actual bridge admission rules.
The pinned OpenSSH helper now excludes system-wide host-key trust, DNS host-key
verification and automatic host-key updates. The installed-build manifest,
daemon integrity identity and cutover manifest now include the SSH helper
SHA-256. These are source-level corrections; live Mac acceptance is still
required before declaring either direction of remote SSH operational.

## One Mac invocation from an existing dirty checkout

The currently known Mac checkout contains unpublished work on a non-main
branch. Running the prior release script there would fail its clean-source
check, and resetting, cleaning, or force-checking out main would be unsafe.
Use the operator-only source launcher added in
`shell_bridge/v6_launch_once.sh`, **without modifying the existing checkout**.

After reviewing the canonical remote `main`, execute one interactive command
in a Mac Terminal:

```bash
git -c core.hooksPath=/dev/null -C /Users/tom/tom-repos/projects/llm-git-bridge fetch origin refs/heads/main:refs/remotes/origin/main && /bin/bash -c "$(git -C /Users/tom/tom-repos/projects/llm-git-bridge show origin/main:shell_bridge/v6_launch_once.sh)" -- /Users/tom/tom-repos/projects/llm-git-bridge
```

The launcher validates the canonical GitHub origin and exact fetched commit,
creates a private detached worktree under
`~/.local/state/bridge-v6-release-sources/<source-sha>`, checks the source
is clean and identical to fetched `origin/main`, then hands off to the existing
one-run guarded release controller. It never resets, cleans, stashes, checks
out, commits or overwrites the original Mac checkout.

A partial or interrupted release source remains for reconciliation; the
launcher refuses to reuse an existing source directory. A successful source
preparation **does not** mean a successful cutover. Native approvals, any
operator-required SSH enrollment and separate-device inbound SSH witness
remain unavoidable live checks. Do not automate these approvals.

For source qualification only, `V6_RELEASE_DRY_RUN=1` exercises creation
of an isolated source worktree without launching the release controller.
No dry-run output claims production acceptance.

## Local Git hook isolation during source preparation

The bootstrap's initial `git fetch` now explicitly disables the local
`reference-transaction` and other Git hooks as well. Fetching an updated
remote-tracking ref can invoke an untracked local hook before the worktree is
prepared, so protecting only `worktree add` was insufficient. Both steps
operate with `core.hooksPath=/dev/null`; neither modifies the user's
existing Git hooks or unpublished working files.



A Git checkout's `.git/hooks/post-checkout` directory is local-only state,
not controlled by canonical GitHub `main`. Preparing a new detached worktree
can otherwise execute a stale, unpublished local hook before any migration
approval. The isolated source launcher forces `core.hooksPath=/dev/null` for
its `git worktree add` invocation so the release source does not execute
those local checkout hooks. Mac regression coverage installs a deliberately
side-effecting local test hook and confirms it was not invoked.

This prevents one known local code-execution path during source preparation.
It does not claim that arbitrary Git filters, system-wide executable
configuration or third-party credential helpers have been audited or that
a GitHub checkout is a sandbox. Production activation remains guarded.

## Approval UI ownership isolation

The source installer now assigns each installed v6 LaunchAgent a unique,
deterministic macOS application bundle identifier derived from that agent's
exact label. Staging and production approval applications must not share a
Launch Services identity: otherwise `open -a` can reuse or activate an
already-running approval app whose `ApprovalRoot` belongs to the wrong
staging instance. Each app's Info.plist still binds its own approval root and
helper executable. A regression test **executes the installer's real plist
generator** twice and verifies distinct staging/production bundle identities,
stable repeat identity, and correctly isolated approval roots.

This prevents one known ambiguity, but it does not replace the required live
native approval DENY/ALLOW tests on the owner's Mac before v5 can stop.
