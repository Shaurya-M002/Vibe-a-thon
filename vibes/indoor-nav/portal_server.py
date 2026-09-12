"""Serve the apartment explorer and SQLite playback API on localhost."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import unquote, urlsplit

from recording_store import DEFAULT_DATABASE, recordings

ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, database=DEFAULT_DATABASE, **kwargs):
        self.database = database
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status, payload, *, head=False):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def respond(self, *, head=False):
        path = unquote(urlsplit(self.path).path)
        if path == "/api/recordings":
            try:
                payload = recordings(self.database)
                if not payload["sessions"]:
                    raise ValueError("No recordings")
                self.send_json(200, payload, head=head)
            except (sqlite3.Error, ValueError, TypeError):
                self.send_json(
                    503,
                    {
                        "error": "No readable playback database. Run "
                        "python demo.py or provide --database."
                    },
                    head=head,
                )
            return
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/apartment/")
            self.end_headers()
            return
        # Expose frontend assets only, never raw databases, recordings, or source.
        asset = Path(self.translate_path(path)).resolve()
        public = (ROOT / "apartment").resolve()
        if (
            not asset.is_relative_to(public)
            or any(
                part.startswith(".") or part == "evidence"
                for part in asset.relative_to(public).parts
            )
            or asset.suffix.lower() in {".sqlite", ".db", ".mp4", ".mov"}
        ):
            self.send_error(404)
            return
        if head:
            super().do_HEAD()
        else:
            super().do_GET()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def do_GET(self):
        self.respond()

    def do_HEAD(self):
        self.respond(head=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    handler = partial(Handler, database=args.database)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Open http://127.0.0.1:{args.port}/apartment/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
