#!/usr/bin/env bash
set -euo pipefail

# Sweep the org for pull requests waiting only on the owner: green checks,
# no conflicts, a current reviewer approval, and no owner review since.
# Those are the funnel's stage-four arrivals — everything cheaper has
# already passed. Bump each at most once per BUMP_HOURS and keep the
# dashboard issue current.

# Configuration. A shared definitions file is on the roadmap; until then,
# every tunable and identity lives here.
ORG="runedeck"
OWNER="N4M3Z"
REVIEWER="runeseer"
BOT_LOGIN="runewright[bot]"
STALE_HOURS=20
BUMP_HOURS=20
DASHBOARD_REPO="runedeck/seer"
DASHBOARD_TITLE="Awaiting owner review"
BUMP_PREFIX="Awaiting the owner"
STATE_APPROVED="APPROVED"
STATE_CHANGES="CHANGES_REQUESTED"
ROLLUP_GREEN="SUCCESS"
MERGEABLE_BLOCKED="CONFLICTING"

hours_since() {
    echo $(( ( $(date +%s) - $(date -d "$1" +%s) ) / 3600 ))
}

waiting=""
for repo in $(gh api --paginate "orgs/$ORG/repos" --jq '.[].name'); do
    api="repos/$ORG/$repo"
    for n in $(gh api --paginate "$api/pulls?state=open&per_page=100" \
        --jq '.[] | select(.draft == false) | .number' 2>/dev/null); do
        pr=$(gh api graphql -f query="query {
            repository(owner: \"$ORG\", name: \"$repo\") {
                pullRequest(number: $n) {
                    updatedAt
                    mergeable
                    headRefOid
                    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
                    latestReviews(first: 30) { nodes { author { login } state submittedAt commit { oid } } }
                }
            }
        }" --jq '.data.repository.pullRequest')
        # The reviewer approval must be bound to the CURRENT head, and only
        # an owner review submitted after that approval counts as the owner
        # having spoken — an old review over an old tree answers nothing.
        IFS=$'\t' read -r rollup mergeable updated reviewer_at < <(jq -r \
            --arg reviewer "$REVIEWER" --arg approved "$STATE_APPROVED" '
            .headRefOid as $head | [
                (.commits.nodes[0].commit.statusCheckRollup.state // "NONE"),
                (.mergeable // "UNKNOWN"),
                .updatedAt,
                ([.latestReviews.nodes[]
                    | select(.author.login == $reviewer and .state == $approved and .commit.oid == $head)
                    | .submittedAt] | last // "")
            ] | @tsv' <<<"$pr")
        owner_after=0
        if [ -n "$reviewer_at" ]; then
            owner_after=$(jq -r --arg at "$reviewer_at" --arg owner "$OWNER" \
                --arg approved "$STATE_APPROVED" --arg changes "$STATE_CHANGES" '
                [.latestReviews.nodes[]
                    | select(.author.login == $owner
                        and (.state == $approved or .state == $changes)
                        and .submittedAt > $at)] | length' <<<"$pr")
        fi
        age=$(hours_since "$updated")
        # Stage four means stage four: green checks, no conflicts, a live
        # reviewer approval of THIS head, and the owner's word unspoken
        # since that approval.
        if [ "$rollup" = "$ROLLUP_GREEN" ] && [ "$mergeable" != "$MERGEABLE_BLOCKED" ] \
            && [ -n "$reviewer_at" ] && [ "$owner_after" = "0" ] && [ "$age" -ge "$STALE_HOURS" ]; then
            waiting="$waiting $ORG/$repo#$n(${age}h)"
            last_bump=$(gh api --paginate "$api/issues/$n/comments" \
                --jq "[.[] | select(.user.login == \"$BOT_LOGIN\" and (.body | startswith(\"$BUMP_PREFIX\")))] | last | .created_at // empty")
            if [ -z "$last_bump" ] || [ "$(hours_since "$last_bump")" -ge "$BUMP_HOURS" ]; then
                gh api -X POST "$api/issues/$n/comments" \
                    -f body="$BUMP_PREFIX's review: this pull request cleared every lane ${age}h ago. The merge waits only on @$OWNER." >/dev/null
            fi
        fi
    done
done
echo "waiting:${waiting:- none}"

dashboard=$(gh api "repos/$DASHBOARD_REPO/issues?state=open&creator=$BOT_LOGIN" \
    --jq "[.[] | select(.title == \"$DASHBOARD_TITLE\")] | first | .number // empty")
if [ -n "$waiting" ]; then
    body="Pull requests that cleared every lane and wait only on the owner's review, oldest first:$(printf '\n')$(printf '%s' "$waiting" | tr ' ' '\n' | grep -v '^$' | sed 's/^/- /')"
    if [ -n "$dashboard" ]; then
        gh api -X PATCH "repos/$DASHBOARD_REPO/issues/$dashboard" -f body="$body" >/dev/null
    else
        gh api -X POST "repos/$DASHBOARD_REPO/issues" -f title="$DASHBOARD_TITLE" -f body="$body" >/dev/null
    fi
elif [ -n "$dashboard" ]; then
    gh api -X PATCH "repos/$DASHBOARD_REPO/issues/$dashboard" -f state=closed >/dev/null
fi
