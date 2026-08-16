# Log: collapsed source disclosure

Date: 2026-08-13 15:31 CEST
Area: Cardine / chat presentation

## Summary

Collapsed assistant source chips behind one native disclosure that is closed by
default. The summary shows the truthful total, for example `14 fonti
verificate`, and expands in place to the existing locator chips.

The renderer treats `Altre N citazioni verificate.` as count metadata rather
than a source. It adds that number to the visible unique locator count and does
not render a misleading extra chip.

## Files Changed

- `src/cardine/demo/ai-primitives.js`: closed-by-default source disclosure,
  deduplicated locator rows, and omitted-source total.
- `src/cardine/demo/ai-primitives.css`: compact summary control, rotated caret,
  bounded expanded layout, and existing chip grid.
- `tests/unit/demo/test_ai_primitives.py`: public renderer contract for default
  collapsed state and truthful aggregate count.

## Verification

- Red proof: renderer initially emitted an always-visible source footer.
- Focused primitive and browser-asset tests: 21 passed.
- Remaining browser contract lane: 4 passed, 6 socket-sandbox skips, 1 known
  stale flashcard-marker test deselected.
- Node syntax, Ruff, and diff checks: passed.
- Visual checks: closed state occupies one compact row; open state has no
  horizontal overflow (`scrollWidth == clientWidth == 688`).
- Fresh visual critique: no clipping, overflow, or unwanted wrapping. The
  expanded inline locator grid remains dense and repetitive; this is recorded
  for the deferred richer source drawer. The visible accent outline is the
  product-wide keyboard focus indicator and was intentionally preserved.

## Notes

- This is an inline disclosure, not the deferred `Visualizza fonti` drawer.
- Canonical presentations and citation identities are unchanged.
