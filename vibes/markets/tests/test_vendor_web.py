"""Exercise merchant HTTP boundaries without network calls or wallet spending."""

import threading

import httpx
import pytest

from governor.vendor.server import Vendor, make_vendor_server


@pytest.fixture
def merchant(tmp_path):
    vendor = Vendor(tmp_path)
    server = make_vendor_server(vendor, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    with httpx.Client(base_url=origin, headers={"Origin": origin}) as client:
        yield vendor, client
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_vendor_serves_console_but_never_wallet_or_order_database(merchant):
    vendor, client = merchant
    page = client.get("/")
    assert page.status_code == 200
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/assets/vault.png").headers["content-type"] == "image/png"
    for path in ("/wallet.json", "/orders.sqlite3", "/../server.py", "/assets/../app.js"):
        assert client.get(path).status_code == 404
    state = client.get("/api/state").json()
    assert state["address"] == vendor.address
    assert state["revenue_atomic"] == "0"
    assert state["orders"] == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://untrusted.example"},
        {"Origin": "null"},
        {"Host": "untrusted.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Content-Type": "text/plain"},
    ],
)
def test_vendor_rejects_cross_origin_orders_before_creation(merchant, headers):
    vendor, client = merchant
    result = client.post("/api/summary", json={"id": "a" * 64, "text": "Hello"}, headers=headers)
    assert result.status_code == 403
    assert vendor.state()["orders"] == []


def test_vendor_rejects_invalid_orders_and_reconciliation_paths(merchant):
    vendor, client = merchant
    for payload in ([], {}, {"id": "bad", "text": "Hello"}, {"id": "a" * 64, "text": ""}):
        assert client.post("/api/summary", json=payload).status_code == 400
    assert client.get("/api/orders/invalid").status_code == 400
    assert client.get("/api/orders/" + "a" * 64).json()["status"] == "NOT_FOUND"
    assert vendor.state()["orders"] == []
