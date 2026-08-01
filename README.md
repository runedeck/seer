# seer

The org's review and observation center. Every review lane the runedeck
ceremony runs — the adjudicating correctness lane, the label cascade that
walks the funnel, the autofix pair, the first-contribution greeting, the
issue triage — lives here once, as a reusable workflow, and every
repository carries only a thin caller pinned at `runedeck/seer@main`.
The org-wide veto monitor runs here natively and keeps the "Awaiting
owner review" queue issue.

The funnel: a pull request passes cursor, then macroscope, then
runeseer, each stage summoned only after the previous settles clean, and
only clean-verdict work reaches the owner. The canonical specification
lives in the skeleton's `docs/specs/review-ceremony/`; this repository
is the machinery, not the canon.

## Caller contract

Callers own the triggers, the concurrency, and the permissions; bodies
own the logic and declare the secrets they need explicitly. First-party
`runedeck/*` references ride `@main` by design — the trust boundary is
push access to this repository, and its history answers for every lane.

## Identities

`runewright` acts (labels, comments, patches, greetings; contents and
workflows write). `runeseer` reviews (contents read-only; its APPROVE is
the earned approval). The split is the review-integrity boundary: the
identity that can write content holds no approval role, and the identity
that approves cannot write content.
