# Install seer's lanes into the org

> Wire the org so seer's reusable lanes can run from any repository's thin callers.

Seer needs no build and no deploy of its own. Installing it means giving the org the secrets, labels, and dashboard state the lanes assume, then letting callers reference `runedeck/seer@main`.

OBJECTIVE: every repository with lane callers can summon a full funnel round.

DONE WHEN: the bare `review` label runs Macroscope, then summons Runeseer on a ready pull request. An unlabeled push summons nothing.

TODO:

- [ ] Org secrets exist with All-repositories visibility: `RUNESEER_APP_ID`, `RUNESEER_APP_KEY`, `RUNEWRIGHT_APP_ID`, `RUNEWRIGHT_APP_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`
- [ ] Both apps are installed on the org: runeseer (contents read AND write, pull requests and issues write), runewright (contents and workflows write, pull requests and issues write)
- [ ] Configure Macroscope through the skeleton's `docs/guides/review-lanes-configuration.md`.
- [ ] Enable Macroscope core correctness and add `review:macroscope` under **Always Review PR Labels**.
- [ ] Observe an actual core correctness check on a test pull request.
- [ ] Set `MACROSCOPE_CORRECTNESS_CHECK` to that check's exact name in each repository or its trusted organization variable.
- [ ] Configure optional Cursor and CodeRabbit reviews through the same guide when required.
- [ ] The target repository carries the caller stubs (scaffolded from the skeleton's `templates/base`)
- [ ] Ceremony labels exist in the target repository (the first pr-lint run provisions them)

Steps:

```sh
gh secret set RUNESEER_APP_ID --org runedeck --visibility all --body "<id>"
gh secret set RUNESEER_APP_KEY --org runedeck --visibility all < runeseer.pem
gh secret set RUNEWRIGHT_APP_ID --org runedeck --visibility all --body "<id>"
gh secret set RUNEWRIGHT_APP_KEY --org runedeck --visibility all < runewright.pem
gh secret set CLAUDE_CODE_OAUTH_TOKEN --org runedeck --visibility all --body "<token from: claude setup-token>"
```

Dashboard state cannot be scripted. Follow the guide named above and verify with its procedure: a draft pull request pushed twice summons nothing, and one `review` label runs the funnel in order.

## Verify provider evidence

Macroscope documents `success` for a clean correctness review and `neutral` for a review with findings.
Its [bug-detection documentation](https://docs.macroscope.com/bug-detection-and-fixes) describes this contract.
Its [approvability documentation](https://docs.macroscope.com/approvability) permits approval when correctness is disabled or has no reviewable code.
An approvability approval therefore cannot prove correctness execution, even when its summary marks Correctness complete.

The exact core correctness check name remains a deployment prerequisite.
Seer supplies no guessed default. An unset variable produces a configuration blocker and preserves the request.
Verify the observed check's product before assigning its name. A custom agent is not core correctness.
Pass the variable to each cascade caller:

```yaml
with:
    macroscope_correctness_check: ${{ vars.MACROSCOPE_CORRECTNESS_CHECK }}
```

Bugbot uses the optional `review:cursor` request.
Its caller supplies an owner token through `RUNEWRIGHT_GITHUB_TOKEN` for the summon comment.
Cursor Security Agent has separate execution and completion evidence.
A successful summon proves dispatch only. Verify the resulting Bugbot review separately.
The default cascade needs no Bugbot token.

## Requirements and permissions

- Cascade callers grant `contents: read`, `checks: read`, `issues: write`, and `pull-requests: write`.
- Optional Bugbot callers grant `pull-requests: write` and `issues: write` for current-head reads and request consumption.
- Both lanes consume requests with `github.token` after successful dispatch. They never fall back to an App token.
- Caller concurrency cancels only on a new `review` request. Consumption through an `unlabeled` event never cancels its run.
- HTTP 401 means authentication was rejected. HTTP 403 means access was denied and requires permission or policy inspection.
- The observed label-consumption HTTP 403 remains unverified after these changes. Existing `issues: write` did not explain that failure.
- Required approvals count only write-access reviewers: an app holding `contents: read` submits APPROVED reviews that satisfy no ruleset. runeseer therefore carries `contents: read and write`, and the write/approve separation is enforced by its workflow's tool allowlist rather than by the permission bit.
- runeseer carries `checks: write`. The controller publishes the `ledger` check run on `reviewed_sha` with it and rerequests `owner-seal` after a generation bump. Without it the ledger step fails and no seal can bind.
- App permission changes take two acceptances: editing the registration (`organizations/<org>/settings/apps/<app>/permissions`) does nothing until the installation accepts the new scope (`organizations/<org>/settings/installations/<id>`). The installations API shows what is actually live.
- Nothing is retroactive: reviews and tokens carry the scope in force when they were minted. After a permission change, mint a fresh approval with a new round.

## Operating constraints

- `claude-code-action` refuses `track_progress` on labeled events, and labels are the standard review request: the flag is gated with `github.event.action != 'labeled'` so progress tracking runs everywhere the action supports it.
- The hosted runner's `gh` predates `--slurp`: paginate with per-page `--jq` arrays merged through `jq -s 'add'`.
- A failed cascade or Bugbot dispatch preserves its request. Repair the cause, then rerun the failed workflow on the final head.
- A failed or uncertain consumption can follow a successful dispatch. Inspect existing downstream runs or summon comments before retrying.
- A changed head requires a new request on the final head. An earlier review cannot satisfy that request.
- The correctness workflow manages its own verdict-round labels. Its failure recovery remains separate from dispatch recovery.
- A failed correctness round posts one exact-head Runeseer failure notice.
  `issue:rune` remains the authoritative blocker.
  The notice links the failed workflow and never copies review output.
  A successful current-head round removes the notice and blocker before approval.
  Inspect the Actions log for the cause.
  Clear the review failure before you request another round.
- Never push after a clean verdict: stale-review dismissal removes the earned approval on any push, so documentation follow-ups ride the next pull request.

EXECUTE NOW: work the TODO list top to bottom, then run the DONE WHEN check on a scratch pull request.
