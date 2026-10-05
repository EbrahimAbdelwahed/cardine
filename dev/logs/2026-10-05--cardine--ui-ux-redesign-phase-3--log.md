# UI/UX redesign — phase 3 (notes as documents, Typst PDF)

Date: 2026-10-05
Branch: `claude/ui-phase3-notes-pdf`, stacked on phase 2 (PR #30)
Spec: [`specs/ui-ux-redesign/README.md`](../../specs/ui-ux-redesign/README.md), ADR-0027

## Outcome

- `cardine.documents.typst_notes`: Markdown → Typst converter with string
  literals only, Cardine notes template, offline `TypstRenderer` (bundled
  fonts, private root, timeout, size bound).
- `RepositoryUiApplication.read_source_pdf` and the `/pdf` route with an
  attachment file name; `features.pdf_export`; materials rows carry `origin`.
- Libreria: Fonti and Note di studio shelves; note rows open in the reader or
  download as PDF; the reader header offers PDF/Scarica.

## Decisions

- The structure lesson picker is not rewritten into a checklist: e2e journeys
  pin its request-identity and retry semantics. Multi-lesson generation is
  available through the PDF checklist and per lesson from the plan outline.
- Typst is optional at runtime; the browser hides PDF when it is missing and
  the endpoint fails explicitly.

## Verification

- Converter unit tests (6, including a real offline render when Typst is
  installed) and PDF export integration tests (3).
- A hostile sample note (Typst syntax, quotes, backslashes, tables, nested
  lists, code) rendered to a correct two-level PDF with the injection printed
  as text.
- Full `pytest`, `ruff`, `mypy`, ownership audit, real-browser e2e.

## Deployment note

`Dockerfile.production` does not install Typst; PDF export there stays
unavailable (503) until a pinned Typst binary is added to the image.
