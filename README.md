# Kharkiv news feed

Merged RSS 2.0 feed of Kharkiv-region news from local outlets.

- Live feed (GitHub Pages): https://ukitfree-wq.github.io/kharkiv-news-feed/news_feed.xml
- Raw file: https://raw.githubusercontent.com/ukitfree-wq/kharkiv-news-feed/main/news_feed.xml
- Odesa city and oblast feed (same engine and rules): https://ukitfree-wq.github.io/kharkiv-news-feed/odesa/news_feed.xml

## How it is updated

`.github/workflows/update-feed.yml` runs the aggregator in `engine/` on GitHub Actions.
It is triggered every 5 minutes by an external cron (cron-job.org calling the
`workflow_dispatch` API), with a `schedule` trigger as a backup.

- `engine/feeds.yaml`: source feeds (add, remove or disable)
- `engine/config.yaml`: region, 30-minute intake window, retention, dedup thresholds
- `engine/title_keywords.txt`: a story is kept only if its title contains one of these words
- Dedup state (`state.db`) lives on the `state` branch as a single commit and is
  restored and saved on every run, so a story is never added twice.
- Feed commits on `main` are squashed once a day to keep the repository small.

`Probe sources` (manual workflow) checks that every source can be fetched and scraped.

## Regions

The workflow runs one job per region in parallel; a failing region does not stop the other.

| Region | Config | State branch | Published file |
|---|---|---|---|
| Kharkiv | `engine/config.yaml`, `engine/feeds.yaml`, `engine/title_keywords.txt` | `state` | `news_feed.xml` |
| Odesa | `engine/regions/odesa/` (`config.yaml`, `feeds.yaml`, `title_keywords.txt`, `title_guards.txt`) | `state-odesa` | `odesa/news_feed.xml` |

A region config is selected with `NEWSFEED_CONFIG=regions/<name>/config.yaml` (relative to `engine/`).
Region-only options: `title_keyword_guards_file` (regexes for headline phrases that must not count
as a keyword hit, e.g. "Подільський район Києва"), `site_overrides` (per-site profile changes),
and in `dedup.semantic`: `incident_places`, `incident_city_prefixes`, `incident_weapons_extra`,
`incident_targets_extra`, `entity_stop_extra`, `digest_title_re`, `topic_rules`.
`engine/ci/publish.sh` takes `STATE_BRANCH`, `STATE_DB`, `FEED_SRC`, `FEED_DEST`, `FEED_LABEL`
and `SQUASH` (only the Kharkiv job squashes the daily history).

### 048.ua relay

www.048.ua returns HTTP 403 to GitHub Actions runners. `engine/ci/feedproxy-048.php` is a
small PHP relay for the user's own web hosting: it only fetches `https://www.048.ua/…` and
`https://048.ua/…`, requires the shared key in the `X-Proxy-Key` header (the copy in the repo has
a placeholder and refuses every request), caches responses for 60 s and gzips its replies.
When the Actions secrets `PROXY_048_URL` (full URL of the uploaded PHP file) and `PROXY_048_KEY`
are both set, the Odesa job routes 048.ua's feed, JSON API and article pages through it
(`engine/newsfeed/proxy.py`); otherwise it fetches directly. Image URLs in the feed stay on
`s.048.ua`.
