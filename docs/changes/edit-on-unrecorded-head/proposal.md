---
adr: "https://github.com/runedeck/deck/blob/main/docs/decisions/DECK-0017%20Sealed%20Review%20Ceremony.md"
status: proposed
---

# Edit on Unrecorded Head

## Why

The open-seal never got a round on cli pull request 91 on 2026-11-25. The open-seal pushed the seal, flipped the draft, and wrote the nonce into the body within seconds. GitHub keeps one waiting run per concurrency group and cancels the older one. The edit runs replaced the push run of the sealed head while it was still waiting ([push run][PUSHRUN]). An edit run starts no round, so the required `review/correctness` check reported `the ledger records no verdict at generation 1; coverage: paid` ([edit run][EDITRUN]). Only the owner's label recovered the head, at the cost of a paid round.

## What Changes

- An edit run that finds no trusted ledger for its head acts as the push run it may have replaced: the triage runs, and a paid round can start.
- The consume step reads that decision from the controller, so such a run consumes the review labels as a push run does.
- An edit on a head that a ledger records is unchanged: it rebuilds the ledger and starts no round.

## Capabilities

- edit-on-unrecorded-head (new)

## Impact

- `.github/workflows/review-correctness.yaml`: the controller step and the consume step.
- `.github/scripts/test_review_controller.py`: one test for an edit on an earlier head, an edit with no ledger, and an edit on a recorded head.
- Every consumer that calls the body at `main`. Each open-seal now gets its round without a label.

[PUSHRUN]: https://github.com/runedeck/cli/actions/runs/45516942512
[EDITRUN]: https://github.com/runedeck/cli/actions/runs/45516958691
