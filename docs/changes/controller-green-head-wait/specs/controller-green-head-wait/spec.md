## ADDED Requirements

### Requirement: Green Head Wait Budget

The controller MUST wait for the deterministic checks on a head within a budget of its own, apart from the budget of the other waits in the job. The default budget MUST outlast the slowest deterministic check that a consumer runs, so a push whose checks end green reaches the triage without an owner label.

#### Scenario: Slow deterministic check ends green inside the budget

- **WHEN** a deterministic check on a pushed head completes green after the other waits in the job would have ended, and inside the green-head budget
- **THEN** the controller triages the head and invites the *[paid lane][LANES]* when the triage admits it

### Requirement: Ledger Read After the Wait

When the controller waited for a green head, it MUST read the review threads, the labels, and the body again before it builds the ledger that the round binds to.

#### Scenario: Body edit arrives during the wait

- **WHEN** the body changes or a review thread arrives while the controller waits for a green head
- **THEN** the ledger records the new body and the thread, and the verdict of the round binds to them

### Requirement: Budget Follows the Body Read After the Wait

When the controller read the body again after a wait, it MUST count the paid budget for the work item that this body names.

#### Scenario: Body names its change during the wait

- **WHEN** the body names a change only after the wait, and that change already spent rounds on its other pull requests
- **THEN** the controller counts those rounds, and refuses a fourth paid round for the work item

[LANES]: https://github.com/runedeck/skeleton/blob/main/docs/specs/glossary.md
