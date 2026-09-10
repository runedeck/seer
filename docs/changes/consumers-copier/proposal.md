---
adr: "https://github.com/runedeck/deck/blob/main/docs/decisions/DECK-0012%20Consumer%20Ceremony%20Synchronization.md"
status: proposed
---

# Consumers Copier

## Why

Seer provisioned a `spec:none` label that no seer workflow reads. The other repositories retired that name for `ignore:spec`, and the weekly parity audit compares every consumer's labels with the template's provisioned set. The decision lives in the deck as DECK-0012.

## What Changes

- `spec:none` leaves the provisioned label list and joins the retired list, so provisioning deletes it on the next run.

## Capabilities

- review-ceremony (new)

## Impact

- `.github/workflows/pr-lint.yaml`.
