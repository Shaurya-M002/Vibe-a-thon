"""Portable store and HTTP tests using synthetic data, never personal exports."""

from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demo import build_demo
from portal_server import Handler
from recording_store import recordings


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = build_demo(Path(self.temp.name) / "recordings.sqlite")

    def test_store_preserves_data_and_orders_points(self):
        before = self.database.read_bytes()
        data = recordings(self.database)
        self.assertEqual(self.database.read_bytes(), before)
        session = data["sessions"][0]
        self.assertEqual(session["id"], 1)
        self.assertIn("Synthetic", session["start"])
        self.assertEqual(len(session["gps"]), 121)
        self.assertEqual(len(session["sensors"]), 7)
        self.assertEqual(session["events"]["2"][0]["delta"], 3)
        for points in session["sensors"].values():
            self.assertEqual([p[0] for p in points], list(range(121)))
        json.dumps(data, allow_nan=False)

    def test_demo_never_overwrites_recordings(self):
        before = self.database.read_bytes()
        with self.assertRaises(FileExistsError):
            build_demo(self.database)
        self.assertEqual(self.database.read_bytes(), before)

    def start_server(self):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(Handler, database=self.database)
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def stop():
            server.shutdown()
            worker.join()
            server.server_close()

        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_port}"

    def test_api_assets_and_private_file_boundary(self):
        root = self.start_server()
        with urlopen(root + "/api/recordings") as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(len(json.load(response)["sessions"]), 1)
        with urlopen(root + "/apartment/") as response:
            self.assertIn(b"NAVSENSE", response.read())
        for path in [
            "/output/recordings.sqlite",
            "/portal_server.py",
            "/apartment/../README.md",
            "/apartment/node_modules/",
        ]:
            with self.assertRaises(HTTPError) as error:
                urlopen(root + path)
            self.assertEqual(error.exception.code, 404)
        with urlopen(Request(root + "/api/recordings", method="HEAD")) as response:
            self.assertEqual(response.read(), b"")

    def test_missing_database_is_actionable_and_not_created(self):
        self.database.unlink()
        root = self.start_server()
        with self.assertRaises(HTTPError) as error:
            urlopen(root + "/api/recordings")
        self.assertEqual(error.exception.code, 503)
        self.assertIn("demo.py", json.load(error.exception)["error"])
        self.assertFalse(self.database.exists())


if __name__ == "__main__":
    unittest.main()
