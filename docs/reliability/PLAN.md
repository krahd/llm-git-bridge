# LLM Bridge Reliability Hardening Plan

Date: 2026-09-20
Job: `bridge-reliability-20260920-a1`
Resource: `reliability/shell-bridge`
Canonical base: `7ff2da2618f68dd9891f012b7006ce0ff2842895`

## Goal

Make the ChatGPT Shell Bridge a reliable transparent transport under sustained multi-conversation use. Routine Drive latency or rclone failure must degrade throughput rather than make the bridge appear dead, accumulate opaque queues, or require manual restarts.

## Established evidence

- macOS launchd already supervises the daemon with RunAtLoad and KeepAlive.
- The daemon remains alive during incidents.
- Runtime logs show repeated 30 s rclone timeouts for mailbox polls and request transfers, plus 5 s health-publish timeouts.
- v5 serializes all rclone operations behind one global transport lock. Worker request download/result publication and daemon poll/health all share that lock.
- With many admitted workers, slow transport creates head-of-line blocking: workers queue transport operations ahead of control-plane polling and health.
- Remote health therefore conflates transport degradation with daemon death and currently omits workers waiting for transport.

## Invariants

1. GitHub `main` remains canonical.
2. Drive/rclone mailbox I/O remains bounded and does not become unconstrained concurrent traffic.
3. Local shell execution may remain concurrent, but transport admission must have explicit backpressure.
4. STARTED/FINISHED, hash binding, crash containment, no-blind-replay and result durability semantics must not weaken.
5. A transport failure before STARTED must leave a request safely pending.
6. A watchdog must never casually kill known active commands.
7. No credential/OAuth configuration is changed silently.
8. Installation remains rerunnable and non-expert friendly.

## Phases

### A. Reproduce and measure
- Add deterministic tests that reproduce control-plane starvation behind slow worker transport.
- Measure poll/health latency and transport waiter behaviour under bursts.
- Preserve a baseline before implementation.

### B. Transport scheduling
- Replace the undifferentiated transport mutex with an explicit serial transport scheduler/gate.
- Give mailbox discovery and health/control operations priority over queued worker transfers while never pre-empting an already-running transport call.
- Add bounded waiters/backpressure so worker count cannot create an unbounded transport backlog.
- Use operation-class timeouts: short discovery/control timeouts; bounded request/result transfer timeouts.
- Evaluate and reuse the repository's existing rclone-RC implementation where it materially reduces process churn and preserves the same safety boundary. Do not add RC merely for novelty.

### C. Decouple transport and execution
- Avoid treating a thread blocked on request download as an actively executing command.
- Make request phases explicit: discovered, transport-wait/download, validated, executing, transport-wait/publish, finished.
- Ensure local execution concurrency is independent from the amount of queued Drive work.

### D. Health and progress semantics
- Maintain a local progress heartbeat independent of Drive publication.
- Report daemon pid/start, loop progress, last successful poll, transport operation/owner/waiters, consecutive transport failures, pending/phase counts and last successful health publication.
- Remote health must describe degraded transport explicitly rather than implying daemon death.
- Log structured, timestamped transport failures and recoveries without secrets.

### E. Automatic supervision
- Add a progress-aware watchdog suitable for launchd.
- Restart only on proven local daemon-progress failure, not merely one slow Drive call.
- Refuse/defers restart while known active commands make restart unsafe, unless crash containment semantics can prove a safe transition.
- Keep launchd KeepAlive as the process-exit safety net.

### F. Installer and operator surface
- Install/update watchdog and daemon idempotently.
- Remove duplicate PATH construction.
- Add a concise doctor/status surface distinguishing daemon, local progress and Drive transport health.
- Keep logs bounded/rotated or otherwise prevent indefinite growth.

### G. Adversarial validation
- Unit tests for transport priority, starvation resistance, timeout/recovery, phase accounting, watchdog policy and installer idempotence.
- Fault tests around every durable boundary and STARTED/FINISHED transition.
- Burst test with many independent read-only requests and injected transport delays.
- Confirm no duplicate execution, no request loss and no manual restart requirement.
- Run full repository tests in bounded groups.

### H. Integrate, deploy, verify
- Checkpoint/push the job.
- `ready` then integrate through the workspace coordinator with repository validation.
- Independently verify GitHub `main`.
- Deploy only the verified canonical version to the Mac.
- Do not disrupt unrelated active bridge commands during deployment.
- Run live acceptance: fresh health, request round-trip, burst behaviour, recovery after bounded transport failure, launchd/watchdog status.

## Completion gates

Complete only when all are true:
- deterministic starvation regression test fails on baseline and passes on the candidate;
- full bridge/workspace/installer test suites pass;
- transport failures remain bounded and observable;
- health distinguishes degraded transport from daemon death;
- progress watchdog policy is tested and safe around active commands;
- GitHub `main` contains the integrated candidate and is independently verified;
- installed runtime matches canonical GitHub content;
- live smoke/burst acceptance completes without manual restart;
- residual OAuth/client-ID risk is either eliminated or explicitly recorded as an external dependency.
