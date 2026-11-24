## ADDED Requirements

### Requirement: Recorded Edit Keeps the Verdict

A body edit starts a run that builds the ledger and starts no round. When the ledger that an earlier run saved for the same head already records the edited body and every live review thread, the controller MUST keep that ledger unchanged, with its generation and its verdict, and start no paid round. A forced round and a run that can start a round MUST NOT take this path.

#### Scenario: Free reviewer finishes before the queued edit run

- **WHEN** a round reads the edited body and judges the head, and a free reviewer such as Macroscope finishes before the queued edit run starts
- **THEN** the edit run keeps the saved ledger
- **AND** the verdict and the approval that it earned stand

#### Scenario: Thread arrives with the edit

- **WHEN** the edit run finds a review thread that the saved ledger does not record
- **THEN** the controller rebuilds the ledger, the generation moves, and the verdict drops
