# Kharkiv news feed

Merged RSS 2.0 feed of Kharkiv-region news from local outlets.

- Live feed (GitHub Pages): https://ukitfree-wq.github.io/kharkiv-news-feed/news_feed.xml
- Raw file: https://raw.githubusercontent.com/ukitfree-wq/kharkiv-news-feed/main/news_feed.xml

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
