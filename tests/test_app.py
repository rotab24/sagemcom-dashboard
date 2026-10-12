import pytest
from fastapi.testclient import TestClient

from app import history
from app.main import app
from app.router import as_list, field


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_helpers():
    assert as_list({"Host": [1, 2]}) == [1, 2]
    assert as_list([3]) == [3]
    assert as_list(None) == []
    assert field({"phys_address": "AA"}, "PhysAddress") == "AA"


def test_status(client):
    r = client.get("/api/status").json()
    assert r["mock"] is True
    assert r["wan"]["up"] is True
    assert -20 < r["gpon"]["rx_dbm"] < -18
    assert 30 < r["gpon"]["temperature"] < 50 and r["gpon"]["voltage"] == 3.18


def test_host_name_skips_mac_placeholder(client):
    h = next(h for h in client.get("/api/hosts").json() if h["mac"] == "a4:83:e7:11:22:33")
    assert h["name"] == "MacBook-Pro" and h["type"] == "PC"


def test_hosts_and_known(client):
    hosts = client.get("/api/hosts").json()
    mac = hosts[0]["mac"]
    history.update_devices(hosts)
    assert client.post(f"/api/hosts/{mac}/known", json={"known": True}).status_code == 200
    assert next(h for h in client.get("/api/hosts").json() if h["mac"] == mac)["known"] is True


def test_wifi_toggle_uses_access_point(client):
    def guest():
        return next(s for s in client.get("/api/wifi").json()["ssids"] if s["alias"] == "WL_GUEST2G4")
    assert guest()["enabled"] is False  # SSID activo pero punto de acceso apagado
    assert client.post("/api/wifi/WL_GUEST2G4", json={"enabled": True}).status_code == 200
    assert guest()["enabled"] is True
    assert client.post("/api/wifi/WL_GUEST2G4", json={"enabled": False}).status_code == 200
    assert guest()["enabled"] is False
    assert client.post("/api/wifi/NO_EXISTE", json={"enabled": True}).status_code == 404


def test_wifi_internal_mesh_is_protected(client):
    assert client.post("/api/wifi/WL_BACKHAUL", json={"enabled": False}).status_code == 400


def test_reboot_requires_confirmation(client):
    assert client.post("/api/reboot", json={"confirm": "si"}).status_code == 400
    assert client.post("/api/reboot", json={"confirm": "REINICIAR"}).status_code == 200


def test_ping_validates_host(client):
    assert client.post("/api/ping", json={"host": "1.1.1.1; rm -rf"}).status_code == 422
    assert client.post("/api/ping", json={"host": "1.1.1.1"}).json()["received"] == 4


def test_history_rates():
    history.record_sample({"bytes_rx": 0, "bytes_tx": 0}, {}, 1, ts=1e10)
    history.record_sample({"bytes_rx": 1000, "bytes_tx": 500}, {}, 1, ts=1e10 + 10)
    rows = [r for r in history.samples(1e11) if r["ts"] >= 1e10]
    assert rows[1]["rate_rx_bps"] == 800
    assert rows[1]["rate_tx_bps"] == 400


def test_automations_listed(client):
    autos = client.get("/api/automations").json()
    assert len(autos) == 4 and all(not a["enabled"] for a in autos)


def test_static_index(client):
    assert "Router" in client.get("/").text


def test_hosts_cached_and_invalidated_by_writes(client):
    from app import router
    calls = []
    orig = router._read_hosts

    def counting():
        calls.append(1)
        return orig()
    router._read_hosts = counting
    try:
        router.invalidate_cache()
        first = client.get("/api/hosts").json()
        client.get("/api/hosts")
        assert len(calls) == 1  # la segunda vez sale de la caché
        uid = first[0]["uid"]
        assert client.post(f"/api/hosts/{uid}/name", json={"name": "Probando caché"}).status_code == 200
        names = {h["uid"]: h["name"] for h in client.get("/api/hosts").json()}
        assert names[uid] == "Probando caché"  # la escritura invalida la caché
    finally:
        router._read_hosts = orig
