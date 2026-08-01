# seer

> The org's review and observation center. Lane logic lives here once; every repository carries thin callers.

## Lanes

| Workflow | Summoned by | What it does |
|----------|-------------|--------------|
| `review-cascade` | bare `review` label | Walks the funnel: posts `bugbot run`, waits for cursor to settle clean, applies `review:macroscope`, waits, applies `review:runeseer`. Stops visibly on findings, a moved head, or a fork after the free lanes. |
| `review-correctness` | `review:runeseer` label | The adjudicating lane: judges the free lanes' findings, reviews the diff, records a machine-readable verdict, and submits the earned approval on an explicit clean verdict for the live head. Consumes the review labels when its round ends. |
| `autofix-suggest` | `review:autofix` label | Untrusted half: runs the fixers against the head with no secrets and uploads a patch artifact. |
| `autofix-comment` | completion of `autofix-suggest` | Trusted half: binds the artifact to the run that built it and posts the patch as a suggestion under runewright. |
| `congrats` | push to `main` | Greets a contributor's first merged pull request. |
| `issue-dedup` | issue opened | Flags probable duplicates, referencing only gathered candidates. |
| `thread-resolver` | push to a pull request | Resolves review threads named by `Resolves-Thread:` trailers, bound to the same pull request. |
| `veto-monitor` | schedule, runs here | Sweeps the org for pull requests that cleared every lane and wait only on the owner; keeps the "Awaiting owner review" issue current. |

## Caller contract

Callers own the triggers, the concurrency, and the permissions; bodies own the logic and declare the secrets they need explicitly. A repository subscribes to events only through its own workflow files, so each repo carries a stub per lane that delegates with `uses: runedeck/seer/.github/workflows/<lane>.yaml@main`. First-party references ride `@main`: the trust boundary is push access to this repository, and its history answers for every lane.

## Identities

runewright acts: labels, comments, patches, greetings, with contents and workflows write. runeseer reviews: contents read only, and its APPROVE is the earned approval. The identity that can write content holds no approval role, and the identity that approves cannot write content.

## Canon

The ceremony specification lives in [skeleton](https://github.com/runedeck/skeleton) under `docs/specs/review-ceremony/`, and the lane dashboard configuration in its `docs/guides/review-lanes-configuration.md`. This repository is the machinery, not the canon.
