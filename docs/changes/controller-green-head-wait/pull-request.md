The controller now waits long enough for a deck head to turn green, so a deck push gets its paid round without the `review` label. Change: docs/changes/controller-green-head-wait.

## Plan

The triage already lives in the correctness controller. Its green-head wait shared the 300-second poll budget, and deck `quality` runs 325 to 505 seconds, so every deck push stood down as "quality is pending". The wait gets its own budget, and the controller reads the pull request again after the wait so an edit during the wait does not make the verdict stale.

## Changes

- Adds `GREEN_HEAD_POLL_ROUNDS` (default 60 rounds of 20 seconds) for the green-head wait in `.github/workflows/review-correctness.yaml`, and raises the job timeout from 40 to 55 minutes.
- Reads the threads, the labels, and the body again after a wait, and recounts the work item's paid budget for that body, in the same controller step.
- Adds a wait fixture and tests for the budget, the second read, the budget recount, and the default budget size in `.github/scripts/test_review_controller.py`.
- Replaces the stale `draft == false` assertion on the entry caller in `.github/scripts/test_runeseer_summary.py`. It left the suite red on `main`.
- Renames the `consumers-copier` change to `retire-unused-waiver-label`, because `rune spec doctor` refuses a two-word id and blocks every push that touches `docs/changes`.

## Testing

- [x] `python3 -m unittest discover -s .github/scripts -p 'test_*.py'`: 319 tests pass. The four new tests fail against the workflow on `main`.
- [x] `bash .githooks/pre-commit --all-files` passes, with `rune spec doctor` green after the rename.
- [x] `rune spec validate controller-green-head-wait` passes.

## Release Notes

- The correctness controller waits up to 20 minutes for the deterministic checks before its triage. A consumer can change this with the `GREEN_HEAD_POLL_ROUNDS` variable.
