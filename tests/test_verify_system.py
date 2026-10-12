import pytest
from fastapi.testclient import TestClient

from app import router, system
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(router, "VERIFY_RETRIES", (0.0,))


def test_writes_are_verified_and_reported(client, no_wait):
    r = client.post("/api/hosts/3/name", json={"name": "Pi"})
    assert r.status_code == 200 and r.headers["X-Router-Verified"] == "1"
    assert "X-Router-Verified" not in client.get("/api/hosts").headers  # una lectura no verifica nada


def test_write_that_does_not_stick_fails(client, no_wait, monkeypatch):
    c = router.get_client()
    real_get_values = type(c).get_values

    def stale(self, xpaths):  # el router "olvida" el cambio
        out = real_get_values(self, xpaths)
        return {k: ("valor-viejo" if "UserFriendlyName" in xpaths[k] else v) for k, v in out.items()}
    monkeypatch.setattr(type(c), "get_values", stale)
    r = client.post("/api/hosts/3/name", json={"name": "Otro"})
    assert r.status_code == 502 and "no se ha aplicado" in r.json()["detail"]
    assert "valor-viejo" in r.json()["detail"]


def test_same_value_normalisation():
    sv = router.same_value
    assert sv(True, True) and sv(True, "true") and sv(False, 0) and sv(86400, "86400")
    assert sv("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff") and sv("1.1.1.1,8.8.8.8", "1.1.1.1, 8.8.8.8")
    assert sv("secreto", "_XMO_WRITE_ONLY_")
    assert not sv(True, False) and not sv("a", "b") and not sv(5, None)


def test_add_and_delete_are_verified(client, no_wait):
    r = client.post("/api/network/reservations", json={"mac": "aa:bb:cc:00:11:22", "ip": "192.168.1.77"})
    assert r.status_code == 200 and r.headers["X-Router-Verified"] == "1"
    alias = next(x["alias"] for x in client.get("/api/network").json()["reservations"] if x["ip"] == "192.168.1.77")
    r = client.delete(f"/api/network/reservations/{alias}")
    assert r.status_code == 200 and r.headers["X-Router-Verified"] == "1"


def test_system_info_and_restart(client, monkeypatch):
    info = client.get("/api/system").json()
    assert info["boot_id"] == system.BOOT_ID and info["pid"] > 0
    launched, timers = [], []
    monkeypatch.setattr(system, "info", lambda: {**info, "can_restart": True})
    monkeypatch.setattr(system, "_launch_restarter", lambda port: launched.append(port))

    class FakeTimer:
        def __init__(self, delay, fn):
            timers.append(fn)

        def start(self):
            pass
    monkeypatch.setattr(system.threading, "Timer", FakeTimer)
    assert client.post("/api/system/restart").status_code == 422  # sin cuerpo JSON no se reinicia
    assert client.post("/api/system/restart", json={"confirm": False}).status_code == 400
    r = client.post("/api/system/restart", json={"confirm": True})
    assert r.status_code == 200 and launched and timers == [system._exit]
