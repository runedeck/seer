## 1. Implementation

- [x] 1.1 Let an edit run that finds no trusted ledger for its head act as a push run
- [x] 1.2 Make the consume step read the controller's decision
- [x] 1.3 Test an edit on an earlier head, an edit with no ledger, and an edit on a recorded head

## 2. Verification

- [x] 2.1 Run the test suite: every test passes, and the new test fails against the workflow on `main`
- [ ] 2.2 Watch the next open-seal start its round without a label after this change merges
