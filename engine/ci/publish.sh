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
#
# Region settings (environment; the defaults are the Kharkiv feed):
#   STATE_BRANCH  branch that holds state.db           (state)
#   STATE_DB      local state database                 (engine/data/state.db)
#   FEED_SRC      feed written by the engine           (engine/output/feed.xml)
#   FEED_DEST     path of the published feed on main   (news_feed.xml)
#   FEED_LABEL    commit message prefix                (Update feed)
#   SQUASH        1 = this job does the daily squash   (1); exactly one job may.
# Several region jobs run in parallel and all push to main, so every publish
# commit is (re)built on a freshly fetched main and the squash uses a lease.
set -euo pipefail

STATE_BRANCH="${STATE_BRANCH:-state}"
DB="${STATE_DB:-engine/data/state.db}"
FEED_SRC="${FEED_SRC:-engine/output/feed.xml}"
FEED_DEST="${FEED_DEST:-news_feed.xml}"
FEED_LABEL="${FEED_LABEL:-Update feed}"
SQUASH="${SQUASH:-1}"

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
[[ -s "$DB" ]] || { echo "ERROR: $DB missing or empty"; exit 1; }
new_blob=$(git hash-object -w "$DB")
old_blob=$(git rev-parse -q --verify "origin/$STATE_BRANCH:state.db" 2>/dev/null || true)
if [[ "$new_blob" != "$old_blob" ]]; then
  tree=$(printf '100644 blob %s\tstate.db\n' "$new_blob" | git mktree)
  state_commit=$(git commit-tree "$tree" -m "state.db $(kiev_now) Europe/Kiev")
  # Only this job writes its state branch (concurrency group), so a force push
  # of the same commit is safe to repeat.
  ok=0
  for ((i = 1; i <= ATTEMPTS; i++)); do
    if git push --force origin "$state_commit:refs/heads/$STATE_BRANCH"; then ok=1; break; fi
    # The ref may have been updated even though the client saw an error.
    if [[ "$(git ls-remote origin "refs/heads/$STATE_BRANCH" | cut -f1)" == "$state_commit" ]]; then ok=1; break; fi
    if (( i < ATTEMPTS )); then backoff "$i"; fi
  done
  [[ "$ok" -eq 1 ]] || { echo "ERROR: state push failed after $ATTEMPTS attempts; feed not published"; exit 1; }
  echo "$STATE_BRANCH branch updated ($(stat -c %s "$DB") bytes)"
else
  echo "state.db unchanged"
fi

# ---- 2. feed on main ----
mkdir -p "$(dirname "$FEED_DEST")"
cp -f "$FEED_SRC" "$FEED_DEST"
items=$(grep -o '<item[ >]' "$FEED_DEST" | wc -l)
when="$(kiev_now) Europe/Kiev"

# Commits after the last one that touched the workflow files are feed updates
# made by the region jobs; they are what the daily squash folds together.
base=$(git log -1 --format=%H -- .github/workflows)
today=$(kiev_date)
first_after_base=$(TZ="$KIEV_TZ" git log --reverse --format=%cd --date=format-local:%F "$base..HEAD" | sed -n 1p)
count_after_base=$(git rev-list --count "$base..HEAD")

changed=1
git diff --quiet -- "$FEED_DEST" && changed=0
# A new region's feed file is untracked until its first publish.
[[ "$changed" -eq 0 && -z "$(git ls-files -- "$FEED_DEST")" ]] && changed=1
need_squash=0
if [[ "$SQUASH" == "1" && "$count_after_base" -gt 1 && -n "$first_after_base" && "$first_after_base" != "$today" ]]; then
  need_squash=1
fi

if [[ "$changed" -eq 0 && "$need_squash" -eq 0 ]]; then
  echo "feed unchanged ($items items); nothing to push"
  exit 0
fi

if [[ "$need_squash" -eq 1 ]]; then
  # The squash is built on a freshly fetched main every attempt (another region
  # job may have pushed meanwhile): main's tree with our feed file replaced,
  # parented on the last workflow commit, pushed with a lease on that main.
  new_feed_blob=$(git hash-object -w "$FEED_DEST")
  msg="Squash feed history: ${items} items, ${when}"
  tmp_index=$(mktemp)
  trap 'rm -f "$tmp_index"' EXIT
  for ((i = 1; i <= ATTEMPTS; i++)); do
    if ! fetch_main; then
      if (( i < ATTEMPTS )); then backoff "$i"; fi
      continue
    fi
    expected=$(git rev-parse refs/remotes/origin/main)
    sq_base=$(git log -1 --format=%H "$expected" -- .github/workflows)
    if [[ "$(git rev-list --count "$sq_base..$expected")" -le 1 ]]; then
      echo "main has nothing left to squash; publishing as a normal commit"
      break
    fi
    GIT_INDEX_FILE="$tmp_index" git read-tree "$expected"
    GIT_INDEX_FILE="$tmp_index" git update-index --add --cacheinfo "100644,$new_feed_blob,$FEED_DEST"
    tree=$(GIT_INDEX_FILE="$tmp_index" git write-tree)
    commit=$(git commit-tree "$tree" -p "$sq_base" -m "$msg")
    folded=$(git rev-list --count "$sq_base..$expected")
    # --force-with-lease: only replaces main if it is still the commit we squashed.
    if git push --force-with-lease="main:$expected" origin "$commit:refs/heads/main"; then
      echo "published (daily squash onto ${sq_base:0:7}, ${folded} commits folded): $msg"
      exit 0
    fi
    fetch_main || true
    if [[ "$(git rev-parse -q --verify refs/remotes/origin/main || true)" == "$commit" ]]; then
      echo "published (daily squash; push reported an error but main is updated): $msg"
      exit 0
    fi
    if (( i < ATTEMPTS )); then backoff "$i"; fi
  done
  echo "WARNING: daily squash not pushed; publishing as a normal commit instead"
fi

# Normal publish: one commit with the new news_feed.xml on top of current main.
msg="${FEED_LABEL}: ${items} items, ${when}"
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
  mkdir -p "$(dirname "$FEED_DEST")"
  cp -f "$FEED_SRC" "$FEED_DEST"
  git add "$FEED_DEST"
  git diff --cached --quiet -- "$FEED_DEST" && return 2
  git commit -q -m "$msg"
  return 0
}

# Always build on a freshly fetched main: the other region job may have pushed.
ready=0
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
echo "ERROR: publishing $FEED_DEST to main failed after $ATTEMPTS attempts"
exit 1
