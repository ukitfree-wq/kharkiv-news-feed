#!/usr/bin/env bash
# Called by .github/workflows/update-feed.yml after `python -m newsfeed`.
# 1) Saves engine/data/state.db to the `state` branch (single force-pushed commit).
# 2) Copies engine/output/feed.xml to /news_feed.xml on main and pushes if changed.
# 3) Once per Kiev calendar day, squashes main's feed commits so the repo stays small.
# Must be run from the repo root on a checkout of main with full history.
#
# Pushes are retried (4 attempts, backoff 5/10/20 s) because GitHub occasionally
# rejects a push server-side ("index-pack failed", "missing necessary objects").
# Before every retry main is re-fetched and the publish commit is rebuilt on top
# of whatever main is now, so a retry never overwrites anybody else's commit.
# Exits non-zero only if every attempt fails. The state push always comes first.
set -euo pipefail

KIEV_TZ="Europe/Kyiv"
kiev_now() { TZ="$KIEV_TZ" date '+%Y-%m-%d %H:%M:%S'; }
kiev_date() { TZ="$KIEV_TZ" date +%F; }
read -ra BACKOFF <<< "${PUBLISH_BACKOFF:-5 10 20}"
ATTEMPTS=4

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# A shallow repository can make GitHub reject a push with "missing necessary
# objects"; make sure we push from complete history.
ensure_full_history() {
  if [[ "$(git rev-parse --is-shallow-repository)" == "true" ]]; then
    echo "repository is shallow; unshallowing before push"
    git fetch --no-tags --unshallow origin || git fetch --no-tags --depth=2147483647 origin
  fi
}

backoff() {  # $1 = attempt number that just failed (1-based)
  local s=${BACKOFF[$(( $1 - 1 ))]:-20}
  echo "push attempt $1/$ATTEMPTS failed; retrying in ${s}s"
  sleep "$s"
}

fetch_main() {
  git fetch --no-tags origin +refs/heads/main:refs/remotes/origin/main
}

ensure_full_history

# ---- 1. state branch (must succeed before the feed is published) ----
DB="engine/data/state.db"
[[ -s "$DB" ]] || { echo "ERROR: $DB missing or empty"; exit 1; }
new_blob=$(git hash-object -w "$DB")
old_blob=$(git rev-parse -q --verify "origin/state:state.db" 2>/dev/null || true)
if [[ "$new_blob" != "$old_blob" ]]; then
  tree=$(printf '100644 blob %s\tstate.db\n' "$new_blob" | git mktree)
  state_commit=$(git commit-tree "$tree" -m "state.db $(kiev_now) Europe/Kiev")
  # Only this job writes the state branch (concurrency group), so a force push
  # of the same commit is safe to repeat.
  ok=0
  for ((i = 1; i <= ATTEMPTS; i++)); do
    if git push --force origin "$state_commit:refs/heads/state"; then ok=1; break; fi
    # The ref may have been updated even though the client saw an error.
    if [[ "$(git ls-remote origin refs/heads/state | cut -f1)" == "$state_commit" ]]; then ok=1; break; fi
    if (( i < ATTEMPTS )); then backoff "$i"; fi
  done
  [[ "$ok" -eq 1 ]] || { echo "ERROR: state push failed after $ATTEMPTS attempts; feed not published"; exit 1; }
  echo "state branch updated ($(stat -c %s "$DB") bytes)"
else
  echo "state.db unchanged"
fi

# ---- 2. feed on main ----
FEED_SRC="engine/output/feed.xml"
cp -f "$FEED_SRC" news_feed.xml
items=$(grep -o '<item[ >]' news_feed.xml | wc -l)
when="$(kiev_now) Europe/Kiev"

# Commits after the last one that touched the workflow files are feed updates
# made by this job; they are what the daily squash folds together.
base=$(git log -1 --format=%H -- .github/workflows)
today=$(kiev_date)
first_after_base=$(TZ="$KIEV_TZ" git log --reverse --format=%cd --date=format-local:%F "$base..HEAD" | sed -n 1p)
count_after_base=$(git rev-list --count "$base..HEAD")

changed=1
git diff --quiet -- news_feed.xml && changed=0
need_squash=0
if [[ "$count_after_base" -gt 1 && -n "$first_after_base" && "$first_after_base" != "$today" ]]; then
  need_squash=1
fi

if [[ "$changed" -eq 0 && "$need_squash" -eq 0 ]]; then
  echo "feed unchanged ($items items); nothing to push"
  exit 0
fi

rebuild_first=0
if [[ "$need_squash" -eq 1 ]]; then
  rebuild_first=1   # if the squash falls back, rebuild on a freshly fetched main
  expected=$(git rev-parse HEAD)
  git add news_feed.xml
  tree=$(git write-tree)
  msg="Squash feed history: ${items} items, ${when}"
  commit=$(git commit-tree "$tree" -p "$base" -m "$msg")
  for ((i = 1; i <= ATTEMPTS; i++)); do
    # --force-with-lease: only replaces main if it is still the commit we squashed.
    if git push --force-with-lease="main:$expected" origin "$commit:refs/heads/main"; then
      echo "published (daily squash onto ${base:0:7}, ${count_after_base} commits folded): $msg"
      exit 0
    fi
    fetch_main || true
    now_main=$(git rev-parse -q --verify refs/remotes/origin/main || true)
    if [[ "$now_main" == "$commit" ]]; then
      echo "published (daily squash; push reported an error but main is updated): $msg"
      exit 0
    fi
    if [[ "$now_main" != "$expected" ]]; then
      echo "main moved during squash (${expected:0:7} -> ${now_main:0:7}); publishing as a normal commit instead"
      break
    fi
    if (( i < ATTEMPTS )); then backoff "$i"; fi
  done
  echo "WARNING: daily squash push failed; publishing as a normal commit instead"
fi

# Normal publish: one commit with the new news_feed.xml on top of current main.
msg="Update feed: ${items} items, ${when}"
# Rebuilds the publish commit on top of the freshly fetched main (never force).
# Returns 0 = commit ready to push, 2 = main already has our commit or the same
# feed (nothing left to do), 1 = fetch failed.
rebuild_on_main() {
  fetch_main || return 1
  local remote; remote=$(git rev-parse refs/remotes/origin/main)
  if [[ "$remote" == "$(git rev-parse HEAD)" && "$(git log -1 --format=%s HEAD)" == "$msg" ]]; then
    return 2
  fi
  git reset -q --hard "$remote"
  cp -f "$FEED_SRC" news_feed.xml
  git add news_feed.xml
  git diff --cached --quiet -- news_feed.xml && return 2
  git commit -q -m "$msg"
  return 0
}

if [[ "$rebuild_first" -eq 1 ]]; then
  ready=0
else
  git add news_feed.xml
  git commit -q -m "$msg"
  ready=1
fi
for ((i = 1; i <= ATTEMPTS; i++)); do
  if [[ "$ready" -eq 0 ]]; then
    rc=0; rebuild_on_main || rc=$?
    if [[ "$rc" -eq 2 ]]; then
      echo "main already has this feed (${items} items); nothing more to push"
      exit 0
    fi
    if [[ "$rc" -eq 0 ]]; then ready=1; fi
  fi
  if [[ "$ready" -eq 1 ]] && git push origin HEAD:refs/heads/main; then
    echo "published: ${items} items"
    exit 0
  fi
  ready=0
  if (( i < ATTEMPTS )); then backoff "$i"; fi
done
echo "ERROR: publishing news_feed.xml to main failed after $ATTEMPTS attempts"
exit 1
