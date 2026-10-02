# Review limit and owner-authorized merges

Date: 2026-10-01 (Europe/Rome).

The owner explicitly requested ending the repeated review cycles, merging the work from chat `01a0f68d-7e6b-7ba3-9c6f-c26d7d8b5e58`, and limiting future review to two rounds per PR. No further semantic review was requested.

## Published merges

- [PR #5](https://github.com/EbrahimAbdelwahed/cardine/pull/5): submitted `24cd4f7a6b4f844ecb58bcd9a9f759235039ab7d`, merged `ab99393a50c1220304b6fc513d65831383c767d5`.
- [PR #3](https://github.com/EbrahimAbdelwahed/cardine/pull/3): submitted `8f72693bac666f62bcf90d07ee20efd3c5b20234`, merged `e52c113133a116c0684df50a6d8b61c790dcf979`.
- [PR #7](https://github.com/EbrahimAbdelwahed/cardine/pull/7): submitted `fedf5ee32439836f6c13086f840083608d411faf`, merged `a9f0e376433a7f4c83b914e38e971f7ff3217da8`.
- [PR #8](https://github.com/EbrahimAbdelwahed/cardine/pull/8): submitted `f795ea04166e90a13d3bea1b7bd8b96a7b3035e0`, merged `f0f295e30f2ea3da3cf62f36baf503a01a59219e`.
- [PR #9](https://github.com/EbrahimAbdelwahed/cardine/pull/9): submitted `1963ed4f89df056dd8ef864c241606ec002338c5`, merged `e6c662f7a0c69a5c745af2e0aea60e7b9ff31020`.

All eight CI matrix checks had successful runs on each submitted head. Existing automatic Codex reviews covered those heads. PR #7 and #9 were retargeted to main after their dependencies merged. Branches and other checkouts were preserved. The prior chat reported 2,614 combined tests passing with four optional skips, build and artifact verification; this task did not rerun the product suite.

## Remaining review findings

The following unresolved automatic-review threads refer to the submitted heads. They remain recorded as findings; this task did not validate or repair them. Older unresolved threads remain available on the PRs. The owner authorized merge without further review.

- PR #5: [Classify connector-only requests as history scoped](https://github.com/EbrahimAbdelwahed/cardine/pull/5#discussion_r4156747385).
- PR #5: [Compare proposal and lifetime events by stream sequence](https://github.com/EbrahimAbdelwahed/cardine/pull/5#discussion_r4156747397).
- PR #3: [Use stream order for the retirement cutoff](https://github.com/EbrahimAbdelwahed/cardine/pull/3#discussion_r4156758308).
- PR #3: [Preserve topics placed before “flashcards”](https://github.com/EbrahimAbdelwahed/cardine/pull/3#discussion_r4156758330).
- PR #3: [Normalize numeric anchors before counting them](https://github.com/EbrahimAbdelwahed/cardine/pull/3#discussion_r4156758350).
- PR #7: [Treat every forced request key as a reused retry](https://github.com/EbrahimAbdelwahed/cardine/pull/7#discussion_r4156762234).
- PR #8: [Order lifetime changes by canonical stream sequence](https://github.com/EbrahimAbdelwahed/cardine/pull/8#discussion_r4156744197).
- PR #8: [Keep short discourse markers from bypassing history validation](https://github.com/EbrahimAbdelwahed/cardine/pull/8#discussion_r4156744222).
- PR #9: [Reclaim orphaned PDF batch reservations](https://github.com/EbrahimAbdelwahed/cardine/pull/9#discussion_r4156746314).

## Review policy change

`AGENTS.md` now permits an initial automatic review and at most one follow-up, counting across commits, chats and agents. After round two, stop the review/fix cycle and report remaining findings and CI to the owner. The limit does not authorize merge or waive failing checks; an explicit owner merge instruction can override the review gate.

Implementation checkout: `/Users/ebrahimabdelwahed/.codex/worktrees/6537/cardine`; branch: `codex/review-round-limit`. Documentation verification: diff and instruction consistency inspection; no runtime contract changed. PR #10 is a separate draft outside the referenced chat.
