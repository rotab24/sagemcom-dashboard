import pytest
from fastapi.testclient import TestClient

from app import automations, history
from app.main import app
from app.router import DAYS, get_client


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def host(client, uid):
    return next(h for h in client.get("/api/hosts").json() if h["uid"] == uid)


def test_block_and_unblock_enables_parental_control(client):
    assert client.get("/api/parental").json()["enabled"] is False
    assert client.post("/api/hosts/3/block", json={"blocked": True}).status_code == 200
    assert host(client, 3)["block"] == "always"
    # Bloquear exige el control parental activo y en modo lista negra
    assert client.get("/api/parental").json() == {"enabled": True, "blacklist_mode": True}
    raw = get_client().get_value('Device/Hosts/Hosts/Host[@uid="3"]/BlacklistedSchedule')
    assert all(day[f"Hours{h}"] is True for day in raw for h in range(24))
    client.post("/api/hosts/3/block", json={"blocked": False})
    assert host(client, 3)["block"] == "none"


def test_schedule_roundtrip(client):
    grid = {d: [False] * 24 for d in DAYS}
    grid["MONDAY"][23] = grid["TUESDAY"][0] = True  # sin Internet lunes 23:00-01:00
    assert client.put("/api/hosts/1/schedule", json={"grid": grid}).status_code == 200
    assert client.get("/api/hosts/1/schedule").json() == grid
    assert host(client, 1)["block"] == "schedule"
    bad = {"MONDAY": [True] * 24}
    assert client.put("/api/hosts/1/schedule", json={"grid": bad}).status_code == 400


def test_missing_day_is_created_with_add_child(client):
    c = get_client()
    c.delete_child('Device/Hosts/Hosts/Host[@uid="2"]/BlacklistedSchedule/ManagementDay[@uid="7"]')  # SUNDAY
    grid = {d: [False] * 24 for d in DAYS}
    grid["SUNDAY"] = [True] * 24
    assert client.put("/api/hosts/2/schedule", json={"grid": grid}).status_code == 200
    assert client.get("/api/hosts/2/schedule").json()["SUNDAY"] == [True] * 24


def test_temporary_block_restores_previous_schedule(client):
    grid = {d: [False] * 24 for d in DAYS}
    grid["FRIDAY"][22] = True
    client.put("/api/hosts/1/schedule", json={"grid": grid})
    assert client.post("/api/hosts/1/block", json={"blocked": True, "minutes": 30}).status_code == 200
    h = host(client, 1)
    assert h["block"] == "always" and h["blocked_until"] is not None
    assert automations.scheduler.get_job("restore-1") is not None
    # Simula que vence el plazo
    p = next(p for p in history.pending_restores() if p["uid"] == 1)
    automations._restore_schedule(1, "test", p["grid"])
    assert client.get("/api/hosts/1/schedule").json() == grid
    assert host(client, 1)["blocked_until"] is None


def test_rename(client):
    assert client.post("/api/hosts/3/name", json={"name": "Servidor casa"}).status_code == 200
    assert host(client, 3)["name"] == "Servidor casa"
    # Sin nombre vuelve al valor por defecto del router (la MAC) y se muestra el HostName
    client.post("/api/hosts/3/name", json={"name": ""})
    assert host(client, 3)["name"] == "raspberrypi"


def test_wifi_details_hide_internal_and_join_names(client):
    d = client.get("/api/wifi/details").json()
    aliases = [n["alias"] for n in d["networks"]]
    assert "WL_BACKHAUL" not in aliases and "WL_GUEST2G4" in aliases
    priv5 = next(n for n in d["networks"] if n["alias"] == "WL_VIDEO_5G")
    assert priv5["clients"][0]["name"] == "MacBook-Pro" and priv5["clients"][0]["down_mbps"] == 866.0
    assert next(r for r in d["radios"] if r["alias"] == "RADIO5G")["possible_channels"][0] == 36


def test_wifi_update_validates(client):
    assert client.patch("/api/wifi/WL_PRIV", json={"password": "corta"}).status_code == 400
    assert client.patch("/api/wifi/WL_PRIV", json={"password": "contraseñaconñ"}).status_code == 400
    assert client.patch("/api/wifi/WL_PRIV", json={"ssid": "x" * 33}).status_code == 400
    assert client.patch("/api/wifi/WL_BACKHAUL", json={"ssid": "hack"}).status_code == 400
    assert client.patch("/api/wifi/NOPE", json={"ssid": "a"}).status_code == 404
    assert client.patch("/api/wifi/WL_PRIV", json={"ssid": "Casa", "password": "nueva-clave-1", "visible": False}).status_code == 200
    n = next(n for n in client.get("/api/wifi/details").json()["networks"] if n["alias"] == "WL_PRIV")
    assert (n["ssid"], n["password"], n["visible"]) == ("Casa", "nueva-clave-1", False)


def test_channel(client):
    assert client.post("/api/radios/RADIO5G/channel", json={"channel": 7}).status_code == 400
    assert client.post("/api/radios/RADIO5G/channel", json={"channel": 36}).status_code == 200
    r = next(r for r in client.get("/api/wifi/details").json()["radios"] if r["alias"] == "RADIO5G")
    assert (r["channel"], r["auto_channel"]) == (36, False)
    client.post("/api/radios/RADIO5G/channel", json={"channel": None})
    r = next(r for r in client.get("/api/wifi/details").json()["radios"] if r["alias"] == "RADIO5G")
    assert r["auto_channel"] is True


def test_qr(client):
    r = client.get("/api/wifi/WL_GUEST2G4/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in r.text
