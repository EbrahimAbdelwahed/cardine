# BeUI chat components in Cardine

Owner request: implement Message Scroller; assess Citations and the message bar.
Reference inspected on 2026-09-28:
- https://beui.dev/components/agents/chat-app
- https://beui.dev/components/agents/message-scroller
- https://beui.dev/components/agents/citations
- https://beui.dev/components/agents/prompt-input

## Message Scroller — implemented adaptation

BeUI combines live-edge following with an optional rail of message previews.
Cardine retains its dependency-free JS shell rather than adding React, Motion,
Tailwind and shadcn for one component. This is an original implementation of
that interaction pattern, not an installed BeUI component or source transplant.

The viewport follows growth while within 56 pixels of the end. Wheel, touch,
keyboard movement and jumps hand control to the reader. Content mutation and
resize observers handle later reflow without pulling a reader out of history.
The rail navigates rendered learner and tutor rows, displays bounded text
previews, tracks the current row and stays hidden when content fits. A return
button resumes following. Explicit submission intentionally reveals the new
learner turn. Refresh preserves position and composer drafts through the existing
morph; old observers/listeners are disconnected when a view is replaced.

Smooth navigation respects reduced motion. The composer remains outside the
scrollable transcript. Nothing in this feature writes canonical state, sends
transcript text to a third party, or invokes a provider.

Owner refinement: reduce the rail's visual footprint by roughly 18% (26px
width, 82% of the previous height cap, and smaller marks). Pointer targets stay
at least 24px tall, or 44px for coarse pointers. Navigation reads message text;
it never adds numbered question/answer prefixes to the transcript. Those prefixes
were only synthetic preview content and have been removed from that preview.

## Citations — adopt presentation selectively in a later change

Current flow:
1. Canonical grounding resolves and validates source evidence in application/core
   code; the browser does not verify claims.
2. Published answer content carries a `Fonti verificate` locator collection.
3. `ui_application._source_viewer_citations` resolves a locator against source
   revision titles, requiring a unique match; it adds source/revision identity,
   viewer kind and the first PDF page when available. This is presentation
   resolution from locator text, not the complete canonical Citation object.
4. `CardineAI.answer` removes that footer from prose, merges DTO references with
   legacy locator labels, and renders a closed disclosure with source chips.
5. Chips with source/revision identity open the authenticated canonical viewer;
   unresolved legacy locators are inert labels. The content endpoint rejects
   missing revisions. Markdown remains escaped and bounded.

BeUI's collapsed, counted list and numbered rows would improve scanning long
collections. Its `CitationItem` is only id/title/domain/url: it does not replace
source/revision/chunk/span validation or authorization. Keep local viewer actions
and page locators instead of external links and favicon requests. Inline markers
should wait for a structured claim-to-citation mapping from the service: the
current footer alone cannot identify which individual sentence is supported.

Concrete existing weaknesses, outside the scroller patch:
- Renderer deduplication uses the display label (`label`, then `title`, then
  `locator`), so distinct canonical revisions sharing the same label collapse.
  A Node reproduction with two revisions and one identical page label produced
  one viewer button. Distinct locator labels from the current DTO are retained.
- Viewer resolution depends on a unique title prefix; ambiguity becomes an inert
  locator rather than an openable canonical reference.
- A singular legacy `citation` can appear in both the answer disclosure and the
  separate provenance chip.

Recommendation: a scoped citations change should deduplicate by canonical
identity plus locator/page, preserve every distinct reference, render numbered
local viewer rows, and test same-title/different-page/revision cases. Keep the
existing closed-by-default disclosure. Only add inline numbers when canonical
claim associations are exposed; never infer them in the browser.

## Message bar — evaluated as BeUI Prompt Input

There is no separate Message Bar in the inspected Agents navigation. The chat's
composer is Prompt Input, which is the interpretation used for this assessment.
It offers an auto-growing textarea, action menu, model selector, keyboard submit,
and animated send/stop states.

Cardine already provides bounded auto-growth, Enter/Shift+Enter/IME handling,
blank/busy validation, draft preservation, source navigation, attached lesson,
and the configured server-owned model. These are useful product contracts to
retain. An action popover could group source/lesson actions if more real actions
are added. Keep the lesson attachment visible.

Do not add a model chooser until the server supports the choices, or a Stop
button until it cancels the actual execution with a truthful terminal outcome.
The inspected Prompt Input source correctly disables stop without an `onStop`
callback; a cosmetic stop that only aborts polling would misrepresent Cardine.
Recommendation: keep the current composer, optionally refine its action surface
in a separate approved UI change; importing the React component offers limited
benefit for the current shell.
