# Owner-approved recovery custody and package target

The owner accepted the recovery modifications on 2026-09-28, explicitly retaining
the requirement to consume Harness as an updatable versioned package rather
than maintain a copy. The approval applies to the 46 exact committed paths in
PR #5 at 50cb0cb. Historical CA-01 classification, CSV ledger, and CA-02 overlay
remain unchanged. A separate recovery overlay binds those bytes and explicitly
admits two new core paths. Only two additional transition AST variances are
accepted, while every accepted path remains protected by its exact digest.

Package adoption remains open: reconcile copied-core changes upstream or into
Cardine adapters; consume a released pinned Harness package; prove installed
artifact parity at CA-08; remove copied-core/transition paths at CA-10. Later
package upgrades must be verified and reproducible. Passing this custody audit
does not complete adoption. No merge is authorized by this decision.
