from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import wifi_advanced
from app.main import app
from app.router import DAYS, get_client

AP = 'Device/WiFi/AccessPoints/AccessPoint[Alias="PRIV0"]'


@pytest.fixture(scope="module")
def client():
    wifi_advanced.SCAN_POLL_SECONDS = 0
    with TestClient(app) as c:
        yield c


def adv(client):
    return client.get("/api/wifi/advanced").json()


def net(client, alias="WL_PRIV"):
    return next(n for n in adv(client)["networks"] if n["alias"] == alias)


def test_overview_hides_internal(client):
    d = adv(client)
    aliases = [n["alias"] for n in d["networks"]]
    assert "WL_BACKHAUL" not in aliases and "WL_PRIV" in aliases
    r5 = next(r for r in d["radios"] if r["alias"] == "RADIO5G")
    assert r5["bandwidths"] == ["20", "40", "AUTO", "80"] and r5["noise_dbm"] == -90 and r5["errors"] == 3


def test_security_isolation_limit(client):
    assert client.put("/api/wifi/advanced/ap/WL_PRIV", json={"security": "WEP"}).status_code == 400
    assert client.put("/api/wifi/advanced/ap/WL_PRIV", json={"max_clients": 500}).status_code == 400
    assert client.put("/api/wifi/advanced/ap/WL_BACKHAUL", json={"isolation": True}).status_code == 400
    body = {"security": "WPA_WPA2_PERSONAL", "isolation": True, "max_clients": 20}
    assert client.put("/api/wifi/advanced/ap/WL_PRIV", json=body).status_code == 200
    n = net(client)
    assert (n["security"], n["isolation"], n["max_clients"]) == ("WPA_WPA2_PERSONAL", True, 20)


def test_mac_filter_diff_and_wps(client):
    url = "/api/wifi/advanced/ap/WL_PRIV/macfilter"
    assert client.put(url, json={"mode": "WHITELIST", "macs": []}).status_code == 400
    assert client.put(url, json={"mode": "BLACKLIST", "macs": ["zz"]}).status_code == 400
    body = {"mode": "BLACKLIST", "macs": ["AA-BB-CC-DD-EE-01", "aa:bb:cc:dd:ee:02"]}
    assert client.put(url, json=body).status_code == 200
    n = net(client)
    assert n["mac_filter"]["mode"] == "BLACKLIST" and n["wps"]["enabled"] is False
    assert sorted(m["mac"] for m in n["mac_filter"]["list"]) == ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]
    raw = get_client().get_value(f"{AP}/MACFiltering/MACAddresses")
    assert raw[0]["MACAddress"] == "AA:BB:CC:DD:EE:01"
    # Quitar una y añadir otra: solo borra/crea lo que cambia
    uid_02 = next(m["uid"] for m in n["mac_filter"]["list"] if m["mac"].endswith("02"))
    client.put(url, json={"mode": "BLACKLIST", "macs": ["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:03"]})
    lst = net(client)["mac_filter"]["list"]
    assert sorted(m["mac"] for m in lst) == ["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:03"]
    assert next(m["uid"] for m in lst if m["mac"].endswith("02")) == uid_02
    client.put(url, json={"mode": "DISABLED", "macs": []})
    assert net(client)["mac_filter"] == {"mode": "DISABLED", "list": []}
    assert client.put("/api/wifi/advanced/ap/WL_PRIV", json={"wps_enabled": True}).status_code == 200
    assert client.post("/api/wifi/advanced/ap/WL_PRIV/wps").status_code == 200


def test_radio_and_band_steering(client):
    assert client.put("/api/wifi/advanced/radio/RADIO2G4", json={"bandwidth": "80MHZ"}).status_code == 400
    assert client.put("/api/wifi/advanced/radio/RADIO2G4", json={"tx_power": 33}).status_code == 400
    assert client.put("/api/wifi/advanced/radio/RADIO2G4", json={"bandwidth": "40MHZ", "tx_power": 60}).status_code == 200
    r = next(r for r in adv(client)["radios"] if r["alias"] == "RADIO2G4")
    assert (r["bandwidth"], r["tx_power"]) == ("40MHZ", 60)
    assert client.put("/api/wifi/advanced/bandsteering", json={"enabled": True}).status_code == 200
    assert adv(client)["band_steering"] is True


def test_scan_and_channel_advice(client):
    r = client.post("/api/wifi/advanced/scan/RADIO2G4").json()
    assert r["networks"][0]["ssid"] == "Vecino" and any(n["ssid"] == "(oculta)" for n in r["networks"])
    # El canal 1 está saturado: debería recomendar 6 u 11
    scores = {x["channel"]: x["score"] for x in r["advice"]["scores"]}
    assert r["advice"]["best"] in (6, 11) and scores[1] > scores[11]
    assert client.post("/api/wifi/advanced/scan/NOPE").status_code == 400


def test_wifi_schedule_applied_by_dashboard(client):
    grid = {d: [False] * 24 for d in DAYS}
    grid["MONDAY"][3] = True  # lunes 3:00-4:00 apagada
    assert client.put("/api/wifi/advanced/schedule/WL_GUEST2G4", json={"enabled": True, "grid": grid}).status_code == 200
    assert net(client, "WL_GUEST2G4")["schedule"]["enabled"] is True

    def state():
        return next(s for s in client.get("/api/wifi").json()["ssids"] if s["alias"] == "WL_GUEST2G4")["enabled"]

    client.post("/api/wifi/WL_GUEST2G4", json={"enabled": True})
    wifi_advanced.apply_schedules(datetime(2026, 10, 12, 3, 0))  # lunes 3:00
    assert state() is False
    wifi_advanced.apply_schedules(datetime(2026, 10, 12, 4, 0))
    assert state() is True
    bad = {"enabled": True, "grid": {"MONDAY": []}}
    assert client.put("/api/wifi/advanced/schedule/WL_GUEST2G4", json=bad).status_code == 400
    # Desactivar para no interferir con otros tests
    client.put("/api/wifi/advanced/schedule/WL_GUEST2G4", json={"enabled": False, "grid": grid})
