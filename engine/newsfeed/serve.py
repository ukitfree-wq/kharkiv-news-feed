"""Tiny local HTTP server for the generated RSS feed.

Default bind address is 127.0.0.1 so the feed is not exposed publicly.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from newsfeed.config import load_config

HELP = """\
pl-news-feed local server

  GET /              RSS feed
  GET /feed.xml      RSS feed
  GET /news_feed.xml RSS feed
  GET /health        plain "ok"
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the aggregated RSS feed over HTTP (localhost by default).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1, not public)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    args = parser.parse_args(argv)

    cfg = load_config()
    feed_path = Path(cfg["paths"]["output_feed"])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *a) -> None:
            sys_stdout = __import__("sys").stdout
            sys_stdout.write("%s - %s\n" % (self.address_string(), fmt % a))
            sys_stdout.flush()

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._send(200, b"ok\n", "text/plain; charset=utf-8")
                return
            if path in {"/", "/feed.xml", "/news_feed.xml", "/rss.xml"}:
                if not feed_path.is_file():
                    self._send(404, b"feed not generated yet\n", "text/plain; charset=utf-8")
                    return
                body = feed_path.read_bytes()
                self._send(200, body, "application/rss+xml; charset=utf-8")
                return
            self._send(404, HELP.encode("utf-8"), "text/plain; charset=utf-8")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving {feed_path} on http://{args.host}:{args.port}/feed.xml", flush=True)
    print("This bind is local-only unless you pass a public --host.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
