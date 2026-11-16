## 1. Implementation

- [x] 1.1 Give the green-head wait its own budget and raise the job timeout
- [x] 1.2 Read the threads, the labels, and the body again after a wait, and count the budget for that body
- [x] 1.3 Add the wait fixture and the tests for the budget, the second read, and the budget recount
- [x] 1.4 Remove the stale draft assertion on the entry caller

## 2. Verification

- [x] 2.1 Run the test suite: every test passes, and the new tests fail against the workflow on `main`
- [ ] 2.2 Watch the next deck push start a paid round without a label after this change merges
