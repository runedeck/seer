---
adr: "https://github.com/runedeck/deck/blob/main/docs/decisions/DECK-0017%20Sealed%20Review%20Ceremony.md"
status: proposed
---

# Edit Keeps Verdict

## Why

A clean verdict went stale on two pull requests on 2026-11-24 with nothing new to judge. On cli pull request 84 a round judged `6d8abce5` at generation 2 and approved it. Macroscope then wrote its summary into the pull request body, and the `edited` event queued a second controller run behind the round. Before that run started, the Macroscope check run went from `pending` to `completed`. The status change alone raised the ledger to generation 3 and cleared the verdict. An edit run starts no round, so the required `review/correctness` check failed with "the ledger records no verdict at generation 3" ([edit run][CLIEDIT]). Skeleton pull request 58 failed the same way at generation 2 ([edit run][SKELEDIT]). A rerun replays the same `edited` event and fails again, so only the owner's label recovers the head, at the cost of a paid round.

The round had already read the edited body in both cases. The edit had nothing left to record.

## What Changes

- An edit run that is not forced compares the ledger it built with the ledger that an earlier run saved for this head. When both record the same head, the same body digest, and no thread the saved ledger lacks, the run keeps the saved ledger byte for byte, writes its generation, and starts no paid round.
- A new thread or a new body still rebuilds the ledger and drops the verdict. Push, ready, and label runs are unchanged.

## Capabilities

- edit-keeps-verdict (new)

## Impact

- `.github/workflows/review-correctness.yaml`: the controller step.
- `.github/scripts/test_review_controller.py`: one test for the kept ledger, the late thread, and a run that can judge.
- Every consumer that calls the body at `main`.
- Out of scope: on deck pull request 86 the Macroscope edit came after the round last read the body, so the body digest changed and the approval went stale by the current rule. Keeping the verdict there means leaving the Macroscope summary block out of the body digest, which narrows that rule. The owner decides that separately.

[CLIEDIT]: https://github.com/runedeck/cli/actions/runs/45303451016
[SKELEDIT]: https://github.com/runedeck/skeleton/actions/runs/45303469613
