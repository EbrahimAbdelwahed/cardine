# Log: PageIndex lesson integration

Date: 2026-08-12 23:10
Area: cardine / Wave A

## Summary

Connected the qualified PageIndex projection to the local repository without
changing canonical evidence retrieval. Active current Markdown revisions are
queued on open/admission with fixed budgets, processed through the isolated
worker, and exposed through truthful per-revision status. Course lesson search
uses ready structural candidates and falls back to the existing SQLite lexical
index for queued, degraded, failed, or disabled projections.

Selection remains explicit and returns a complete source/revision/span/content/
catalog pin. CLI commands expose status, rebuild, disable/enable, search, and
selection. The browser bootstrap shows a read-only structural-index status and
states that text search remains available in reduced mode.

## Verification

- Repository/CLI/browser load-bearing journey: PageIndex ready after admission,
  exact lesson selection, disable-to-lexical fallback, and restart persistence.
- Existing repository unit suite: 22 passed before the new focused regression.
- Integrated repository, PageIndex, browser-contract, and CLI suite excluding
  the known pre-existing A2 export incompatibility: 48 passed and one expected
  CPython 3.12 qualification-digest skip outside the nested sandbox.
- Ruff, mypy, Node syntax, ownership audit, and scoped diff check: passed.
- Independent Terra re-review confirmed that oversized structural trees
  fail-degrade and startup applies its revision budget before loading blobs.

## Notes

- PageIndex remains navigation-only; SQLite FTS remains the sole evidence
  retriever and canonical citation text still comes from the source ledger.
- Lesson search fails closed rather than silently truncating a course above its
  fixed active-source budget.
