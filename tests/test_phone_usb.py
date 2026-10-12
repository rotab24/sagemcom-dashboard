import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def phone(client):
    return client.get("/api/phone").json()


def test_phone_overview(client):
    p = phone(client)
    assert p["available"] and p["lines"][0]["registered"] is True
    assert p["lines"][0]["features"]["call_waiting"] is True
    assert [c["number"] for c in p["call_logs"]] == ["600111222", "911223344"]


def test_line_features_and_forwarding(client):
    assert client.put("/api/phone/lines/1", json={"forward_always": True}).status_code == 400  # sin número
    assert client.put("/api/phone/lines/1", json={"forward_always": True, "forward_always_number": "abc"}).status_code == 400
    body = {"forward_always": True, "forward_always_number": "600 999 888", "do_not_disturb": True, "call_waiting": False}
    assert client.put("/api/phone/lines/1", json=body).status_code == 200
    f = phone(client)["lines"][0]["features"]
    assert (f["forward_always"], f["forward_always_number"], f["do_not_disturb"], f["call_waiting"]) == (True, "600999888", True, False)
    assert client.put("/api/phone/lines/9", json={"call_waiting": True}).status_code == 404
    assert client.post("/api/phone/ring-test").status_code == 200


def test_call_log_delete(client):
    assert client.delete("/api/phone/calls/1").status_code == 200
    assert [c["uid"] for c in phone(client)["call_logs"]] == [2]
    assert client.delete("/api/phone/calls/1").status_code == 404
    assert client.delete("/api/phone/calls").status_code == 200
    assert phone(client)["call_logs"] == []


def test_usb(client):
    u = client.get("/api/usb").json()
    assert u["devices"][0]["name"] == "SanDisk Cruzer"
    assert u["volumes"][0]["used_pct"] == 40.0 and u["smb"]["enabled"] is True
    assert client.put("/api/usb/smb", json={"enabled": False}).status_code == 200
    assert client.put("/api/usb/dlna", json={"enabled": True, "name": ""}).status_code == 400
    assert client.put("/api/usb/dlna", json={"enabled": False, "name": "Multimedia casa"}).status_code == 200
    u = client.get("/api/usb").json()
    assert u["smb"]["enabled"] is False and u["dlna"] == {"enabled": False, "name": "Multimedia casa"}
    assert client.post("/api/usb/eject", json={"sysfs": "9-9"}).status_code == 404
    assert client.post("/api/usb/eject", json={"sysfs": "1-1"}).status_code == 200
