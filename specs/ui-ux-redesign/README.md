# Cardine UI/UX redesign

Status: owner-requested 2026-10-05 (ADR-0027). Phase 1 is PR #29; phase 2
is stacked on it; phases 3–5 follow in order.

## Goal

Cardine is "NotebookLM, but open and hackable" for exam preparation. The
interface must free cognitive space for studying: calm, ordered, quiet, and
organised around the student's real workflow instead of around the service
architecture.

## The workflow the interface is built around

1. **Onboarding.** Exam date → study plan. Upload sources; background
   processing (conversion, indexing, lesson structure) is visible but never
   blocks the student.
2. **Study.** Ask the tutor about the uploaded material, or select one or more
   lessons of a source and generate study notes from them.
3. **Notes as documents.** Accepted study notes are readable in the app and
   downloadable as PDF rendered with Typst.
4. **Document and agent together.** A note or source is read beside the chat.
   Selecting a passage offers "explain", "ask", "make a card"; tutor citations
   jump back to the exact passage.
5. **Agent parity.** Everything the UI can do, the agent can do on request,
   except handling sensitive data (credentials, provider consent) and accepting
   proposals, which stay explicit HUMAN decisions in the UI.

## Diagnosis of the current interface (2026-10-05)

- **Architecture leaks into the copy.** Kickers and side cards explain internal
  invariants ("fatti attribuiti · nessuna agenda", "request ID", "proiezione del
  corso", "Il browser non è il proprietario dello stato", "operazione
  atomica"). They are true but are not the student's concern.
- **Flat, hidden navigation.** Nine destinations of equal weight, six of them
  inside a collapsible "Spazio di studio" disclosure, icon-only by default.
- **Every page is a two-column grid** whose aside restates the page's rules,
  e.g. "Un'assenza di card non viene sostituita da una coda inventata".
- **Proposals read as database rows** (revision id, session id, "impegni di
  fonte", a diff table with nothing to diff) rather than as content to decide.
- **The exam date is not a product concept.** The setup wizard keeps it only in
  browser memory; the Plan page then reports "Data non configurata".
- **Notes are buried** inside the Sources page under each source row.
- **Reader and chat are separate worlds.** Reading happens in the Sources
  aside; asking happens on another route with no link between the two.
- **Too many typographic voices.** Monospaced uppercase kickers on every block
  compete with the serif display titles.

## Principles

- One primary action per screen; everything else is quieter or one step away.
- Copy talks about studying, never about services, projections or identifiers.
  Provenance remains available on demand (provenance sheet), not inline.
- Single reading column (max ~72ch) for content pages; two panes only where two
  things are genuinely used together (library + reader, document + chat).
- Calm surfaces: paper background, hairlines over boxes, one accent used only
  for primary action and focus. Serif only for page titles; sans for UI; mono
  only for code and keyboard hints.
- Status is shown where the work is (a processing source shows its progress in
  its own row), not in global panels.
- Canonical boundaries are unchanged: the browser still sends commands to the
  canonical services; HUMAN acceptance stays explicit.

## Information architecture

| Rail entry | Route key | Purpose |
| --- | --- | --- |
| Nuova chat | `oggi` | Home: composer, today's work, exam countdown |
| Chat | `sessione` | Current conversation |
| Libreria | `fonti` | Sources + reader + study notes |
| Ripasso | `ripasso` | Due cards |
| Piano | `piano` | Exam date and plan |
| Da approvare | `proposte` | Pending proposals (badge only when pending) |
| Verifiche | `verifiche` | Assessments |
| Progressi | `percorso` | Student journal |

Route keys, element IDs and API endpoints are unchanged; only labels, grouping
and presentation change. The rail shows two always-visible groups ("Studio"
and "Revisione") instead of a collapsible disclosure.

## Phases

### Phase 1 — Visual system, navigation and copy (this branch)

Frontend only, no API change.

- Rail: two labelled groups, no disclosure; renamed labels as above.
- Typography: kickers/eyebrows become sentence-case sans captions; mono stays
  for code and shortcuts only.
- Remove architecture copy and explanatory asides from Oggi, Libreria, Piano,
  Da approvare, Verifiche, Ripasso, Progressi. Inline provenance labels
  ("fonte: scheda del corso") are removed; the provenance sheet remains.
- Home: composer first; a single quiet row for today's work (due reviews,
  pending decisions) replaces the "Panoramica di studio" disclosure.
- Da approvare: proposals render as content cards (type, the card itself,
  accept/reject); revision/session identifiers move to provenance.
- Libreria: a calmer header, compact source rows, the reader as the second
  pane, study notes as a first-class section; the extract/revision registers
  are removed.
- Piano: a single exam-date block (countdown or an honest "not set yet"),
  then goals and constraints as a plain list.

### Phase 2 — Onboarding and exam plan

- `course.study_plan_set@1` (HUMAN or SERVICE, course-scoped, idempotent by
  request key) records the exam date, daily minutes (5–720) and an optional
  objective. Its reducer replaces the profile's exam date; readiness and the
  plan read it like any other canonical fact.
- The lesson outline is derived on read: the verified top-level PageIndex
  spans of each active original source (a lone title wrapper over three or
  more chapters is unwrapped; a heading-free or unstructured source is one
  lesson). Generated, extracted and inferred sources are not lessons.
- A lesson is studied when the student journal holds a `topic_covered` entry
  with the lesson's bounded topic (case and spacing insensitive). "Segna
  studiata" appends that entry; nothing else stores progress.
- The schedule spreads unstudied lessons over the days left in source order;
  the last ~15% of days (1–7) are final review and the exam closes it. With
  fewer lessons than study days, lessons are spaced out and the days between
  them are practice days. Nothing is stored; the browser only names weekdays.
- `GET /api/v1/plan` stays fast (readiness and plan); `GET /api/v1/plan/schedule`
  returns the outline and schedule and loads after the header. Bootstrap adds
  today's lessons using the PageIndex statuses it already reads.
- Onboarding for a fresh course: Esame → Fonti (dropzone, per-source structure
  progress that keeps running in the background) → Piano preview. A course
  already in use is never interrupted; home offers "Imposta la data d'esame".
- Every surface that shows a lesson offers the same actions: Studia (pins the
  lesson and asks the tutor), Genera note (the exact verified span) and Segna
  studiata.

### Phase 3 — Study notes as documents

- "Note" become their own list inside Libreria: lesson multi-select → generate
  → review → accept.
- Typst renderer adapter (server-side `typst compile`, pinned template,
  no network) producing a PDF per accepted note set; download endpoint.

### Phase 4 — Document + agent workspace

- Split view: document (source or note) on the left, chat on the right, sharing
  the lesson pin.
- Selection toolbar on the document: Spiega · Chiedi · Crea card. The passage
  is sent as explicit scope (existing lesson-pin contract), not as evidence.
- Citations in tutor answers open the document at the cited locator.

### Phase 5 — Agent parity

- Expose navigation and UI commands (generate notes for lessons, set exam date,
  start a review, open a document) as agent tools behind the same command
  endpoints. Credentials, consent and proposal acceptance stay UI-only.

## Verification

Each phase: offline pytest suite, ruff, mypy, browser review on desktop and
mobile in light and dark mode, and the existing e2e journeys where available.
