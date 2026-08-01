#!/usr/bin/env bash
set -euo pipefail

# Sweep the org for pull requests waiting only on the owner: green checks,
# no conflicts, a current runeseer approval, and no owner review since.
# Those are the funnel's stage-four arrivals — everything cheaper has
# already passed. Bump each at most once per 20 hours and keep the
# dashboard issue current.

hours_since() {
    echo $(( ( $(date +%s) - $(date -d "$1" +%s) ) / 3600 ))
}

waiting=""
for repo in $(gh api --paginate orgs/runedeck/repos --jq '.[].name'); do
    api="repos/runedeck/$repo"
    for n in $(gh api --paginate "$api/pulls?state=open&per_page=100" \
        --jq '.[] | select(.draft == false) | .number' 2>/dev/null); do
        pr=$(gh api graphql -f query="query {
            repository(owner: \"runedeck\", name: \"$repo\") {
                pullRequest(number: $n) {
                    updatedAt
                    mergeable
                    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
                    latestReviews(first: 30) { nodes { author { login } state } }
                }
            }
        }" --jq '.data.repository.pullRequest')
        IFS=$'\t' read -r rollup mergeable updated runeseer_ok owner_acted < <(jq -r '[
            (.commits.nodes[0].commit.statusCheckRollup.state // "NONE"),
            (.mergeable // "UNKNOWN"),
            .updatedAt,
            ([.latestReviews.nodes[] | select(.author.login == "runeseer" and .state == "APPROVED")] | length),
            ([.latestReviews.nodes[] | select(.author.login == "N4M3Z" and (.state == "APPROVED" or .state == "CHANGES_REQUESTED"))] | length)
        ] | @tsv' <<<"$pr")
        age=$(hours_since "$updated")
        # Stage four means stage four: green checks, no conflicts, a live
        # runeseer approval, and the owner's word still unspoken.
        if [ "$rollup" = "SUCCESS" ] && [ "$mergeable" != "CONFLICTING" ] \
            && [ "$runeseer_ok" -ge 1 ] && [ "$owner_acted" = "0" ] && [ "$age" -ge 20 ]; then
            waiting="$waiting runedeck/$repo#$n(${age}h)"
            last_bump=$(gh api --paginate "$api/issues/$n/comments" \
                --jq '[.[] | select(.user.login == "runewright[bot]" and (.body | startswith("Awaiting the owner")))] | last | .created_at // empty')
            if [ -z "$last_bump" ] || [ "$(hours_since "$last_bump")" -ge 20 ]; then
                gh api -X POST "$api/issues/$n/comments" \
                    -f body="Awaiting the owner's review: this pull request cleared every lane ${age}h ago. The merge waits only on @N4M3Z." >/dev/null
            fi
        fi
    done
done
echo "waiting:${waiting:- none}"

dashboard=$(gh api "repos/runedeck/seer/issues?state=open&creator=runewright[bot]" \
    --jq '[.[] | select(.title == "Awaiting owner review")] | first | .number // empty')
if [ -n "$waiting" ]; then
    body="Pull requests that cleared every lane and wait only on the owner's review, oldest first:$(printf '\n')$(printf '%s' "$waiting" | tr ' ' '\n' | grep -v '^$' | sed 's/^/- /')"
    if [ -n "$dashboard" ]; then
        gh api -X PATCH "repos/runedeck/seer/issues/$dashboard" -f body="$body" >/dev/null
    else
        gh api -X POST "repos/runedeck/seer/issues" -f title="Awaiting owner review" -f body="$body" >/dev/null
    fi
elif [ -n "$dashboard" ]; then
    gh api -X PATCH "repos/runedeck/seer/issues/$dashboard" -f state=closed >/dev/null
fi
