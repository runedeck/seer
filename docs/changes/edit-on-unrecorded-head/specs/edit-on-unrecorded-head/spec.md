## ADDED Requirements

### Requirement: Edit on an Unrecorded Head Starts the Round

A body edit can replace the run of a push that was still waiting, so no run records the pushed head. When the controller finds no trusted ledger for the head of an edit run, it MUST triage that head as it triages a push, and a paid round MAY start. When a trusted ledger records the head, the edit run MUST rebuild the ledger and start no round.

#### Scenario: Open-seal edit replaces the waiting push run

- **WHEN** the owner's open-seal pushes the seal and edits the body before the push run starts, and the edit run replaces that push run
- **THEN** the edit run finds no ledger for the sealed head and triages it as a push
- **AND** the round judges the sealed head without the owner's label

#### Scenario: Edit arrives on a recorded head

- **WHEN** a body edit arrives on a head that a trusted ledger records
- **THEN** the controller rebuilds the ledger and starts no round
