# RepoReach validation and commercial plan

Status: design freeze candidate
Date: 2026-09-18

RepoReach is already justified as infrastructure by real internal use. Commercial validation therefore has a deliberately modest standard: determine whether external users value managed reach and reliability enough to help fund maintenance. It does not require venture-scale market evidence.

## 1. Hypotheses

H1 — There are users whose preferred AI surface and Git execution environment lack a convenient native path.

H2 — Some users value retaining the reasoning/context accumulated in their chosen conversational AI instead of moving the whole task into a different coding agent only for filesystem access.

H3 — Users value choosing repository roots and permissions once while RR hides MCP, Drive, relay and other transport details.

H4 — Exact-base transactions, local validation and explicit Git policy are useful independently of transport.

H5 — A subset of users of a complete open-source RR core will pay a modest monthly fee for managed RepoReach Relay convenience/support.

## 2. Falsifiers

Material negative evidence includes:
- external users consistently solve the problem with native features and do not need RR;
- self-managed RR gets adoption but managed RRR gets no demand;
- RRR setup remains as complex as existing transport configuration;
- browser/vendor compatibility labour exceeds plausible subscription revenue;
- infrastructure cost grows materially faster than revenue;
- most demand is actually for generic machine control rather than Git.

Commercial failure would not make the open-source core a failure.

## 3. Validation stages

### Stage A — internal dogfood

Required evidence:
- repeated real work across several repositories;
- at least one RRR-only route independent of Drive;
- simultaneous multipath duplicate delivery safely deduplicated;
- measured clean-install setup time;
- recovery from daemon restart, relay hibernation and brief network loss;
- verified content deletion under documented TTL.

No billing.

### Stage B — private external alpha

Recruit roughly 3-10 external users with varied AI providers, Git hosts, local-only/remote-backed repositories and workspace environments.

Success signals:
- a majority reach RepoReach: Connected without maintainer intervention;
- setup feels like installing a utility rather than operating infrastructure;
- real Git tasks complete from the user's preferred AI surface;
- support incidents reveal repeatable product defects rather than bespoke networking projects.

### Stage C — public free beta

Publish the open-source core and a bounded hosted RRR beta. Hosted limits are explicit; self-managed RR remains complete.

Measure only service-operational data naturally required by RRR:
- pseudonymous active endpoints;
- operation counts by class;
- relay-induced versus local-core failure categories;
- payload bytes and latency;
- pairing/re-pairing;
- support categories.

Do not use source text, local paths, filenames, command output, commit messages or conversation content as analytics.

## 4. Engineering gates before broad beta

- >=99% of controlled canary requests eventually reach a terminal result when both endpoints remain available;
- zero duplicate Git mutations under retry/reorder/cross-transport campaigns;
- zero successful operations after session revocation;
- zero local-path or credential leakage in automated scans;
- TTL tests prove content deletion;
- forced free-tier exhaustion produces explicit failure, not unbounded queue growth or paid spillover;
- relay hibernation/restart and daemon reconnect pass repeatedly;
- an RRR outage does not damage repositories or disable another route.

These are internal engineering gates, not public SLAs.

## 5. Usability gates

A new user should be able to:
1. install RR;
2. select repository roots;
3. choose semantic permissions;
4. pair a browser/AI surface;
5. see discovery complete;
6. complete a read;
7. complete an edit/commit where enabled;
8. diagnose a failure without learning provider-specific plumbing.

Track abandonment points and automate repeated friction.

## 6. Commercial trigger

Do not build billing merely to ask whether people might pay.

Begin a paid RRR experiment when either:
- at least 10 external users are weekly active on RRR and at least 5 have recently said they would pay roughly $15-$20/month; or
- at least 3 external users are willing to become actual paid pilot customers.

Then implement only the minimum payment/entitlement layer needed to charge real customers.

## 7. Pricing hypothesis and target

Initial individual Pro hypothesis: $15-$20/month.

At $20/month:
- 25 users = $500 MRR;
- 50 = $1,000 MRR;
- 100 = $2,000 MRR;
- 125 = $2,500 MRR.

At $15/month, about 167 users yield about $2,500 MRR before fees/cost.

$2,500 MRR is a maintenance-sustaining success target, not a forecast.

## 8. Paid value

Potential paid RRR value:
- higher relay quotas;
- multiple paired endpoints;
- easier credential recovery/rotation;
- managed relay updates;
- optional longer service-level audit metadata without source retention;
- priority support;
- team/device features only after demand.

Remain free/self-managed:
- discovery;
- transaction engine;
- local policies;
- validation commands;
- commit/push where enabled;
- protocol documentation;
- non-RRR transports.

## 9. Zero-cash guardrail

Before revenue, use infrastructure whose free plan hard-stops rather than silently bills. Re-check vendor quotas immediately before deployment and impose lower internal per-endpoint quotas.

Do not buy a custom domain or activate paid object storage, monitoring, databases or auth merely for polish before users exist.

An unavoidable recurring-cost service should be introduced only when settled recurring revenue comfortably exceeds it. Planning guardrail:

> settled recurring revenue >= 3 x unavoidable recurring infrastructure cost

Variable per-user costs must also remain comfortably below revenue.

## 10. Support economics

Human support is likely a larger cost than compute. Classify alpha/beta support:
- installation defect;
- transport/browser compatibility;
- Git/policy misunderstanding;
- repository edge case;
- vendor outage/change;
- bespoke consulting.

Repeated issues become product work. A subscription must not silently become unlimited bespoke consulting.

## 11. Decision checkpoints

Continue open-source maintenance while RR remains useful internally and manageable.

Continue RRR free beta while external users gain value and marginal cost remains low enough to learn.

Introduce a paid pilot only after the commercial trigger.

Pay for infrastructure only after observed usage identifies a real free-tier limit and revenue supports it.

Add multi-device/team administration only after repeated demand.

Do not broaden RR beyond Git; generic files/desktops/databases/machine control are separate product questions.

## 12. Evidence ledger

For each validation stage record:
- date and version;
- cohort size/recruitment source;
- AI/client and transport surfaces;
- setup funnel;
- aggregate success/error metrics;
- support categories;
- explicit willingness-to-pay evidence without unnecessary personal data;
- infrastructure usage/cost;
- resulting decision.

This keeps future decisions evidence-based rather than anecdotal.
