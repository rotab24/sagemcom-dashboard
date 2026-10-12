import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import automations, extras, history
from app.main import app
from app.router import DAYS


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _kinds(since):
    return [e["kind"] for e in history.events(100, since=since)]


def test_monitor_internet_and_router(client):
    t0 = time.time()
    wan_down = {"up": False, "status": "DOWN"}
    gpon = {"rx_dbm": -19.5}
    extras.monitor(wan_down, gpon, [], now=datetime(2026, 10, 8, 12, 0))
    extras.monitor(wan_down, gpon, [], now=datetime(2026, 10, 8, 12, 1))  # no repite
    extras.monitor({"up": True}, gpon, [], now=datetime(2026, 10, 8, 12, 30))
    k = _kinds(t0)
    assert k.count("internet_down") == 1 and k.count("internet_up") == 1
    extras.monitor_unreachable(RuntimeError("timeout"))
    extras.monitor_unreachable(RuntimeError("timeout"))
    extras.monitor({"up": True}, gpon, [])
    assert _kinds(t0).count("router") == 2  # cae y vuelve, sin duplicados


def test_monitor_fiber_and_intruder(client):
    t0 = time.time()
    history.set_setting("fiber_alerted", None)
    extras.monitor({"up": True}, {"rx_dbm": -29.0}, [], now=datetime(2026, 10, 8, 12, 0))
    extras.monitor({"up": True}, {"rx_dbm": -29.5}, [], now=datetime(2026, 10, 8, 13, 0))  # una vez al día
    assert _kinds(t0).count("fiber") == 1
    intruder = {"mac": "de:ad:be:ef:00:01", "name": "Desconocido", "ip": "192.168.1.66", "active": True}
    extras.monitor({"up": True}, {"rx_dbm": -19}, [intruder], now=datetime(2026, 10, 8, 14, 0))  # de día: nada
    extras.monitor({"up": True}, {"rx_dbm": -19}, [intruder], now=datetime(2026, 10, 9, 3, 0))
    extras.monitor({"up": True}, {"rx_dbm": -19}, [intruder], now=datetime(2026, 10, 9, 3, 5))
    assert _kinds(t0).count("intruder") == 1


def test_groups(client):
    assert client.put("/api/extras/groups/Niños", json={"macs": []}).status_code == 400
    macs = ["a4:83:e7:11:22:33", "3C:22:FB:44:55:66", "00:00:00:00:00:00"]
    assert client.put("/api/extras/groups/Niños", json={"macs": macs}).status_code == 200
    g = client.get("/api/extras/groups").json()[0]
    assert g["name"] == "Niños" and len(g["members"]) == 3 and g["members"][0]["uid"] is None  # MAC inexistente
    assert client.post("/api/extras/groups/Niños/block", json={"blocked": True}).status_code == 200
    hosts = {h["mac"]: h for h in client.get("/api/hosts").json()}
    assert hosts["a4:83:e7:11:22:33"]["block"] == "always" and hosts["3c:22:fb:44:55:66"]["block"] == "always"
    assert client.post("/api/extras/groups/Niños/block", json={"blocked": True, "minutes": 30}).status_code == 200
    hosts = {h["mac"]: h for h in client.get("/api/hosts").json()}
    assert hosts["a4:83:e7:11:22:33"]["blocked_until"] is not None
    client.post("/api/extras/groups/Niños/block", json={"blocked": False})
    grid = {d: [h >= 22 for h in range(24)] for d in DAYS}
    assert client.put("/api/extras/groups/Niños/schedule", json={"grid": grid}).status_code == 200
    hosts = {h["mac"]: h for h in client.get("/api/hosts").json()}
    assert hosts["a4:83:e7:11:22:33"]["block"] == "schedule"
    assert client.post("/api/extras/groups/Nope/block", json={"blocked": True}).status_code == 404
    assert client.delete("/api/extras/groups/Niños").status_code == 200
    assert client.get("/api/extras/groups").json() == []


def test_guest_wifi(client):
    assert client.post("/api/extras/guest", json={"hours": 100}).status_code == 400
    assert client.post("/api/extras/guest", json={"hours": 2, "bands": []}).status_code == 400
    st = client.post("/api/extras/guest", json={"hours": 2}).json()
    assert st["active"] and len(st["password"].split("-")) == 3 and st["until"] > time.time()
    wifi = {s["alias"]: s for s in client.get("/api/wifi").json()["ssids"]}
    assert wifi["WL_GUEST2G4"]["enabled"] and wifi["WL_GUEST5G"]["enabled"]
    assert automations.scheduler.get_job("guest-off") is not None
    extras._guest_expired()  # simula que vence
    wifi = {s["alias"]: s for s in client.get("/api/wifi").json()["ssids"]}
    assert not wifi["WL_GUEST2G4"]["enabled"] and client.get("/api/extras/guest").json() == {"active": False}


def test_report(client):
    history.record_sample({"bytes_rx": 0, "bytes_tx": 0}, {"rx_dbm": -19.0}, 3, ts=time.time() - 120)
    history.record_sample({"bytes_rx": 2e9, "bytes_tx": 5e8}, {"rx_dbm": -20.0}, 3, ts=time.time() - 60)
    r = client.get("/api/extras/report?days=7").json()
    assert r["total_down_gb"] >= 2.0 and r["fiber"]["min"] <= -20.0
    assert r["outages"] >= 1 and isinstance(r["per_day"], list)
    extras.weekly_report_push()
    assert history.events(1)[0]["kind"] == "report"


def test_automation_editor(client):
    actions = client.get("/api/automations/actions").json()
    assert {"wifi", "block_group", "guest_wifi", "speedtest"} <= set(actions)
    bad = [{"name": "x", "cron": "esto no", "action": "wifi", "params": {}}]
    assert client.put("/api/automations", json=bad).status_code == 400
    assert client.put("/api/automations", json=[{"name": "x", "cron": "0 1 * * *", "action": "nope"}]).status_code == 400
    items = [{"name": "Cena", "cron": "0 21 * * *", "action": "block_group", "params": {"group": "Niños", "minutes": 45}, "enabled": True},
             {"name": "Apagado", "cron": "0 3 * * *", "action": "reboot", "enabled": False}]
    assert client.put("/api/automations", json=items).status_code == 200
    listed = client.get("/api/automations").json()
    assert [a["name"] for a in listed] == ["Cena", "Apagado"]
    cena = listed[0]
    assert cena["id"].startswith("auto-") and cena["next_run"] is not None and listed[1]["next_run"] is None
    # Editar conservando el id: no se duplica el trabajo programado
    cena["cron"] = "30 21 * * *"
    assert client.put("/api/automations", json=[cena, listed[1]]).status_code == 200
    trigger = str(automations.scheduler.get_job(cena["id"]).trigger)
    assert "hour='21'" in trigger and "minute='30'" in trigger
    assert sum(1 for j in automations.scheduler.get_jobs() if j.id == cena["id"]) == 1
