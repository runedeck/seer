## ADDED Requirements

### Requirement: Unused Waiver Retirement

Label provisioning SHALL NOT create a waiver label that no workflow in the repository reads, and SHALL retire such a label when it exists.

#### Scenario: Provisioning runs after the unused label retires

- **WHEN** the provisioning job runs with `spec:none` in the retired list
- **THEN** the live `spec:none` label is deleted and no workflow reads it
