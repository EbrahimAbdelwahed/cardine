# Sources layout — 2026-10-02

Owner requested redesign of the disordered Sources screen. Dedicated managed
checkout: `sources-layout/cardine`; branch: `codex/sources-layout`; fetched
GitHub default base: `origin/main` at `1f49efd` (merged PR #14). The shared
`codex/notes-generation` checkout and its dirty files were left untouched.

The real screen compressed source titles and revision text letter by letter:
four row columns competed inside the narrow library column. The new page has a
shared header, a library/reader workspace, grouped source titles and format/count
metadata, wrapping actions, a compact empty reader, and a separate notes section.
Import, excerpts and revision records use native disclosures. Canonical revision
and checksum remain available through each source's provenance drawer. Read-only
canonical endpoints, escaping, server-owned authority, human decisions and API
contracts are preserved. Selected source controls expose their state, mobile
opening moves focus to the reader, and failed Markdown/text reads retry the same
canonical document. PDF continues through the native authenticated viewer.

`#material-jobs`, generation attributes and lesson form selectors remain the
integration seam for the parallel PageIndex lesson-picker activity. This branch
does not implement PageIndex inference, selection or request identity. The
presentation binding announces preparation and brings an existing lesson editor
into view when preparation succeeds.

The owner instruction authorizes these two UI paths. Their current bindings in
`selected-lesson-notes-overlay.json` are refreshed to exact final bytes; the two
non-UI bindings and historic recovery/source-study/journal overlays are preserved.
Prior bindings remain in Git history. This records implementation custody, not
review approval or installed-Harness parity.

Visual inspection used the real dependency-free app served from a disposable
synthetic repository with offline model fixtures. Direct inspection at desktop
1280×720 and mobile 390×844 covered library, reader, import and provenance; the
browser regression additionally measures 320, 390, 800, 1100 and 1440 px with
both rail widths, long titles, open import, selected state, focus and failed-read
retry. The 320 px native file-input overflow was reproduced and fixed.
Screenshots live outside Git at `/private/tmp/cardine-sources-*.jpg`. No local
reviewer was launched, following the owner's GitHub-only review instruction.
No live provider calls, deployment, or merge. Verification and publication
results follow below.

Initial validation: 142 focused UI/material/browser tests pass, and the two
additional responsive/empty/error regressions pass. Ruff, JS syntax, diff check,
full mypy (663 files), working-tree ownership audit (322 rows), and isolated
sdist/wheel build pass. An earlier full run produced 2,870 passes and four
optional/live skips; its only failure was the new test's non-serializable CDP
wait, now corrected. Final committed-tree full validation remains pending.
