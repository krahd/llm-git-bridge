# Remote execution authority: v6 readiness policy

## Current support and limitations

The v6 request mailbox is the transport for remote invocation of **local Mac shell
commands**. A ChatGPT/Codex provider can submit terminal commands to the daemon.
The daemon has bounded timeout, output, process-group containment, durable
request IDs and crash ambiguity semantics, and a macOS filesystem/network sandbox
for ordinary requests. Within an allowed Git worktree, reversible changes use
repository scope without an ordinary approval dialog. Read-only commands require
no approval. Requests seeking unrestricted system shell are denied.

**Raw SSH/SCP/SFTP/rsync is not yet a usable v6 capability.** The shell sandbox
denies its network access, and a popup cannot change this rule. The bridge now
rejects these unbounded network operations *before* prompting rather than
pretending confirmation would make them work.

## Intended friction-minimizing authorization

| Operation | Default |
| --- | --- |
| Read-only inspections inside allowed roots | Execute, no popup |
| Reversible Git/worktree edits with sandbox network denied | Execute, no popup; journal and verify |
| Destructive or irreversible local operation | Explicit consequence-specific confirmation; never rely on a broad allow-all |
| Credential/secrets access, external side effects | Separate narrow installed capability; independent confirmation if high impact |
| Raw arbitrary SSH command or arbitrary remote target | Deny, no cosmetic popup |
| Host- and action-pinned SSH maintenance/read-only inspection | **Implemented for fixed `status` and `identity` actions, but not provisioned or live-qualified** as an opt-in trusted helper |
| Remote irreversible SSH mutations (deployment, deletion, restart) | Require exact host/operation/scope approval, durable ID and visible rollback plan |

## Host-pinned SSH helper requirements before v5 retirement

A separately provisioned helper must:

1. Use a local operator-owned host/action allowlist that agents cannot modify.
   Resolve host aliases through pinned hostname, port, username, and known host
   key; never enable StrictHostKeyChecking=no, arbitrary ProxyCommand, or agent-
   supplied SSH options. Prefer a dedicated restricted key/account.
2. Take only a registered action token, **not** arbitrary remote shell text,
   via the established trusted-operation schema. Map each action to a fixed
   pre-reviewed remote program/argv and privilege level.
3. Avoid prompting for routine bounded remote *read* operations, only after
   the host/key/action set has been explicitly installed by the Mac operator.
   Privileged or irreversible remote operations continue to require exact
   transaction approval. This requires a deliberate policy upgrade: current
   trusted-operation descriptors always require approval.
4. Give every call a durable operation ID, bounded wall-clock timeout and
   output, and a host-key-verified SSH session. Network timeouts are
   **indeterminate**, not permission to auto-replay.
5. Reject unknown hosts, changed host keys, unexpected identities, ambiguous
   responses, and commands not on the approved inventory. Log the pinned
   identity, action, request hash, timestamp, exit state and confirmation, but
   redact credentials and sensitive output.
6. Qualify on macOS with a disposable SSH test endpoint: successful read,
   operator denial, hostname/key mismatch, no-network failure, timeout while
   remote effects may have happened, retries, user/key rotation, and isolation
   from non-allowlisted hosts. Run an explicitly approved live acceptance
   probe before claiming production SSH ready.

**Do not sunset v5 based only on the ordinary shell smoke and approval GUI
tests.** v6 must demonstrate the actual remote terminal flows currently used by
the ecosystem and the agreed bounded SSH workflow, or explicitly document SSH
as a deferred non-critical feature with the operator's consent.

The one-invocation local release script proves isolated mailbox transport,
read-only terminal execution and critical local approvals, but it is not
sufficient evidence that remote SSH maintenance is qualified.

## Implemented optional pinned SSH status capability

The repository includes `shell_bridge/ssh_pinned_helper.py`. It takes only
`status` or `identity` and an administrator-provisioned policy directory,
running fixed `uptime` or `id -un` against one pinned host. Unknown
commands, host names, options, unsafe policy permissions, missing host keys,
and network errors fail closed. SSH is noninteractive, no tunnels/forwarding,
no user SSH configuration, strict pinned known-host verification, no arbitrary
provider-controlled command and bounded time/output.

An operator may set `REGISTER_PINNED_SSH_POLICY_DIR` while performing an
**out-of-band installation**. The directory must contain an owner-controlled
`ssh-policy.json`, a matching dedicated `known_hosts`, and a configured
identity file. The installed descriptor pins the helper executable's SHA-256,
policy directory and just those two action names. The installer records explicit
consent so those two low-impact, immutable read-only actions no longer prompt
per call. Unrelated trusted helpers and consequential commands still require
their native approval. Default installations do **not** enable SSH.

This is **not** general remote terminal SSH, remote deployment, file transfer,
or privilege escalation. Do not treat the optional status helper as evidence
that broad SSH work is accepted or that the previous v5 workflows have been
replaced. Configuration and a live known-host and identity acceptance test
remain necessary on the owner's Mac.

## Second independent command-risk review

An adversarial review of the shell-command classifier found that repository
scope does not imply reversibility: deleting an untracked file with plain
`rm` or `unlink` may be irreversible despite the Git sandbox. The direct
command-risk classifier now requests explicit confirmation for such deletions
(including without `-r` or `-f`). It also recognizes common split-flag
`git clean -d -f`, `git -C ... reset --hard`, and recursive delete variants.
Routine `git status`, `git diff`, `git clean -n` and tracked `git rm`
remain distinct.

**This is a heuristic and cannot fully classify arbitrary shell programs.**
A wrapper program, dynamic shell construction, Python script or build tool
can perform an effect that is not syntactically visible in the submitted
command. Filesystem and network sandboxing, coordinator isolation, worktree
checkpoints and external-side-effect restrictions remain essential; an agent
must not describe high-impact classification alone as an irreversible-action
security boundary. Do not treat local source tests as a substitute for live
recovery and final operator acceptance.

## Adversarial SSH output exhaustion hardening

The original host-pinned SSH helper used subprocess.run(capture_output=True).
A compromised or misbehaving remote endpoint could stream unbounded stdout or
stderr until the 25-second timeout: checking a 64-KiB cap *after* subprocess
completion does not bound memory use. The helper now drains output incrementally
with selectors, imposes a hard 64-KiB cap separately on each stream while the
process is running, enforces the original elapsed-time deadline and stops the
local SSH process group on timeout or excess output. Both outcomes are
non-successful and must never cause automatic replay. Regression tests exercise
large real child-process stdout/stderr streams and a sleeping child without
opening any remote network connection.
