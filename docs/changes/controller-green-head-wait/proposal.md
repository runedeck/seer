---
adr: "https://github.com/runedeck/deck/blob/main/docs/decisions/DECK-0017%20Sealed%20Review%20Ceremony.md"
status: proposed
---

# Controller Green Head Wait

## Why

The controller never starts a paid round on a deck pull request, so every merge needs the owner's label or a forced merge. On a push, the controller waits for the `quality` check before its triage. That wait used the five-minute budget of the other waits in the job, and the deck's `quality` job runs from 325 to 505 seconds on its October 2026 pull request runs ([quality runs][QUALITY]). The push on deck pull request 83 stood down with the coverage `free lanes only: deterministic check quality is pending` ([push run][PUSHRUN]), and no later event triaged the head after it turned green.

The same pull request shows the second fault. The agent edited the body during the wait. The queued edit run found the head green and triaged it as paid, and an edit starts no round, so the required check reported no verdict with coverage `paid` ([edit run][EDITRUN]). A round that the push run had admitted would still have gone stale. The controller read the threads and the body before the wait, so the verdict bound to a body that no longer existed.

## What Changes

- The green-head wait gets its own budget, 60 poll rounds of 20 seconds by default. A consumer can set the `GREEN_HEAD_POLL_ROUNDS` repository variable. The job timeout rises from 40 to 55 minutes to keep room for the model call.
- After a wait, the controller reads the review threads, the labels, and the body again, and counts the paid budget for the work item that the new body names, before it builds the ledger.
- A head that is green on the first read is read once, as before.
- A stale assertion leaves the suite. The entry caller sends a same-repository draft to the controller, because the green draft starts the funnel.

## Capabilities

- controller-green-head-wait (new)

## Impact

- `.github/workflows/review-correctness.yaml`: the controller step and the job timeout.
- `.github/scripts/test_review_controller.py`: a wait fixture and the tests for the budget, the second read, and the budget recount.
- `.github/scripts/test_runeseer_summary.py`: the entry caller's draft assertion.
- Every consumer that calls the body at `main`. A push now holds its runner while the deterministic checks run, up to the budget.

[QUALITY]: https://github.com/runedeck/deck/actions/workflows/quality.yaml
[PUSHRUN]: https://github.com/runedeck/deck/actions/runs/40791683282
[EDITRUN]: https://github.com/runedeck/deck/actions/runs/40791879368
