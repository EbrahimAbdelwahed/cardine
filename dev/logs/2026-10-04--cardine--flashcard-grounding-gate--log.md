# Flashcard documentary support gate

Owner task 5, based on origin/main 6e9c904 (PR #22). That PR published retrieval
experiments only; production retrieval is unchanged here too.

## Integration and authority

Cardine owns `application/flashcard_grounding.py`. `_generation_service` wraps
only the hybrid/morphology integrity validator in RuntimeRegistries. Schema-only
validation remains schema-only; full core schema, plan, hierarchy and citation
validation runs first. The gate preserves the successful candidate batch and
validator contract, and terminates ON failures before PlaybookEngine success,
child proof, lesson page checkpoint or canonical proposal registration. Scoped
composition clones propagate the same policy and judgement port. No second
canonical write path exists; HUMAN acceptance is unchanged.

Each request includes the question and every answer block (label, text and all
key points), plus only that card's cited canonical excerpts from its prepared
evidence envelope. Content is resolved again; retired, superseded, excluded and
foreign chunks cannot supply support. Active membership and exact resolution
are rechecked after provider awaits. No PageIndex summary or assistant prose is
used. Media claims fail closed because this text-only gate cannot verify them.

Choice keys are supported / contradicted / insufficient. Exact option coverage,
finite normalized probabilities, known selection, resolved model identity,
configured probability and margin are checked. Weak judgement, timeout, errors,
missing credentials and denied consent cannot validate ON proposals. Cancellation
propagates. Complete request input is bounded to 64,000 UTF-8 bytes with no
truncation, at most 24 sequential card calls and one configured batch deadline.
There is no regeneration or source-recovery loop. No raw source/provider/error
telemetry is added.

## Configuration

`features.flashcard_grounding_mode`: off (default), shadow, on.
`features.grounding_probability`: 0.9; `features.grounding_margin`: 0.2.
These positive conservative policy choices are not calibrated accuracy claims.
SHADOW performs bounded checks but preserves historical proposal behavior;
SHADOW is not a guarantee of documentary support. Existing v1/v2 reads default
the new feature to OFF and do not rewrite repository files. No user repository
is enabled by this change. Gate policy/model/bounds fingerprint enters the request commitment (and hence
worker/lesson/proof identities) and partitions operational engine storage; old
OFF/SHADOW checkpoints cannot authorize ON execution. Owner/proof storage remains
shared so existing HUMAN proposals remain resolvable across feature changes.
Stable policy identity supports restart/retry.

`cli/repository.py` composes a dedicated adapter using judgement.resolved_model_id,
server-owned environment credentials and ConsentChoiceJudgementPort. It leaves
routing/cardability adapter selection unchanged. Other owners modifying scope
routing can preserve the grounding constructor arguments and `_for_content`
propagation in flashcard_proposals.py. No knowledge/preprocessor changes here.

## Verification and delivery

Offline tests cover both real production profile paths, complete support,
contradiction/partial support, all key points/labels present in input, invalid
Choice coverage/distributions, unresolved model, bounds, manipulated/foreign/
retired/excluded evidence, provider error, denial, cancellation, deadline,
absence of proposals/proof on failure, and idempotent restart/new-turn retry.
Local full/static/package results and PR delivery recorded at closeout below.
No paid calls, rollout, migration, deployment or merge performed.

Local verification: initial complete suite 3,020 passed / four expected skips;
latest focused suite 62 passed, including post-await retirement and consent port.
Ruff, strict mypy (682 files), wheel/sdist build and package verifier pass.
Final complete suite: 3,039 passed / four expected skips, plus the latest
focused suite (62 passed) covering the subsequently added retirement case.
PR https://github.com/EbrahimAbdelwahed/cardine/pull/23 published as draft while
current-head CI runs. Updated only the two current tutor-context hash bindings
and repository-config post-baseline hash in the audit script; frozen ownership
classification/CSV, historical recovery and CA-02 custody overlays remain
intact, and ownership/dispositions are unchanged.
Automatic GitHub review round 1 at 5a2d2c2 found partial grounding-policy
configuration silently defaulted missing fields. Decoder now supplies OFF/default
thresholds only if all three new fields are absent; every partial definition is
rejected. Six offline malformed-shape regressions cover every partial subset.
Round 2 will be the final automatic review for this PR; no local reviewer used.

Review fix validation: 104 focused configuration/gate/production-profile tests
passed; Ruff, strict mypy and ownership audit pass.
