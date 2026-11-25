Lets a body edit on a head that no ledger records start the round that its push run would have started.

## Plan

Change `edit-on-unrecorded-head`: proposal and tasks under `docs/changes/edit-on-unrecorded-head/`. The open-seal pushes, flips the draft, and edits the body within seconds, and GitHub cancels the waiting push run in favor of the newer edit run.

## Changes

- Sets `LEDGER_ONLY` to false in the controller step of `.github/workflows/review-correctness.yaml` when no trusted ledger records the head, and writes it as the `ledger_only` output.
- Makes the consume step read `steps.controller.outputs.ledger_only`, with the event action as the fallback.
- Adds `test_edit_on_an_unrecorded_head_acts_as_the_push_it_replaced` in `.github/scripts/test_review_controller.py`.

## Testing

- `python3 -m unittest discover -s .github/scripts -p 'test_*.py'`: 321 tests pass. The new test fails against the workflow on `main` with "no round starts on an edit".
- `actionlint .github/workflows/review-correctness.yaml` reports nothing.

## Release Notes

- An open-seal gets its review round without the owner's label.
