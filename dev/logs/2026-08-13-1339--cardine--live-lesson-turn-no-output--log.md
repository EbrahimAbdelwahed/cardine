# Log: Live lesson turn produces no grounded output

Date: 2026-08-13 13:39 CEST
Area: Cardine / tutor chat / structural retrieval

## Summary

The live dialogue was reconstructed from canonical session events and durable
capability runs. The learner asked to study lesson 1, but the tutor returned a
promise to start a source-based explanation. The follow-up was treated as a new
request, searched with the content-free query `basata sulla`, and terminated
without evidence.

This is a backend orchestration failure, not primarily a missing browser render:
the fallback presentation was canonically persisted.

## Findings

- Natural study wording such as `studiamo ... di cosa parla?` is not classified
  by the host safety router as an immediate grounded explanation. A deterministic
  replay therefore ends after `tutor_decision.v1` with the model's promise.
- The promise does not create an operational continuation. The learner's
  follow-up loses the prior `lezione 1` target, and retrieval receives
  `basata sulla`, which has no live evidence.
- The live converted PDF uses a structural heading such as `L01_04/03/2025`,
  while lesson resolution currently requires the exact normalized title
  `Lezione 1`. The live resolver therefore reports no structural candidate.
- The live PageIndex projection is queued with `pageindex_limit` and no
  candidates, leaving the exact-title Markdown fallback as the only structural
  route.
- When a structural pin is found in a controlled fixture, structural retrieval
  still fails before the provider call: its citation places canonical text in
  the locator field and omits the quoted snippet required by the evidence
  invariant.

## Verification

- Canonical event inspection: sequences 61–64 contain the original request, the
  promise, the follow-up, and the insufficient-evidence fallback.
- Durable run inspection: the follow-up starts `explain_concept` with query
  `basata sulla`; the capability records zero evidence and stops after search.
- Deterministic one-turn replay against a `Lezione 1` fixture: failed in 1.3 s;
  only `tutor_decision.v1` ran and the promise was returned unchanged.
- Live repository probe: `lezione 1` has five lexical hits but zero lesson
  candidates; `basata sulla` has zero lexical hits; PageIndex reports
  `pageindex_limit`.
- Direct structural retrieval probe: failed with
  `retrieval evidence text must be the canonical quoted snippet`.

## Next Fix Boundary

Keep the correction targeted: repair canonical citation construction, route
natural lesson-study questions directly to the grounded capability, preserve
the prior lesson target if the assistant ever requests confirmation, and teach
lesson navigation the converted `L01` heading convention. Add one regression
for the exact live wording and one for canonical structural evidence before
changing production behavior.

No production code was changed during this diagnosis.
