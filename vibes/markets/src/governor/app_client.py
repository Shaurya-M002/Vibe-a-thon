"""Loopback app client for the embedded Codex launcher and MCP bridge."""

from urllib.parse import urlsplit

import httpx


class AppError(ValueError):
    pass


class AppClient:
    def __init__(self, origin="http://127.0.0.1:8787"):
        url = urlsplit(origin)
        if (
            url.scheme != "http"
            or url.hostname not in ("127.0.0.1", "localhost")
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise AppError("Governor URL must be a loopback HTTP origin")
        self.origin = f"http://{url.hostname}:{url.port or 8787}"

    def request(self, path, payload=None):
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=125) as client:
                with client.stream(
                    "GET" if payload is None else "POST",
                    self.origin + path,
                    json=payload,
                    headers={"Origin": self.origin, "Content-Type": "application/json"},
                ) as response:
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 8_000_000:
                            raise AppError("Governor report exceeds 8 MB")
                    if response.status_code not in (200, 202):
                        raise AppError(
                            f"Governor HTTP {response.status_code}; "
                            "inspect the existing session before retrying"
                        )
                    import json

                    return json.loads(content)
        except httpx.HTTPError as exc:
            raise AppError(
                "Governor unavailable. Start governor-web; reuse the same session "
                "and call IDs after a lost response."
            ) from exc

    def report(self, sid):
        return self.request("/api/sessions/" + sid)

    def link(self, sid):
        return f"{self.origin}/#sessions/{sid}"
