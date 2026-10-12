from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import maintenance
from app.config import ROOT
from app.main import app


@pytest.fixture(scope="module")
def client():
    maintenance.POLL_SECONDS = 0
    with TestClient(app) as c:
        yield c


def test_overview(client):
    o = client.get("/api/maintenance").json()
    assert o["time"]["servers"] == ["212.230.255.2", "hora.rediris.es"]
    assert o["dyndns"]["services"] == ["DynDNS", "No-IP"] and o["dyndns"]["service"] == "DynDNS"
    assert o["remote_access"]["trusted"] == ["212.166.64.0/24"] and o["remote_access"]["ssh"] is False
    assert o["info"]["internal_firmware"] == "v2.35.0"


def test_diagnostics(client):
    assert client.post("/api/maintenance/traceroute", json={"host": "1.1.1.1; ls"}).status_code == 400
    tr = client.post("/api/maintenance/traceroute", json={"host": "1.1.1.1"}).json()
    assert [h["ip"] for h in tr["hops"]] == ["192.168.144.1", "1.1.1.1"]
    assert client.post("/api/maintenance/nslookup", json={"host": "cloudflare.com"}).json()["result"]["Answer"] == "1.1.1.1"
    st = client.post("/api/maintenance/speedtest").json()
    assert st["last"]["download"] == 612.4 and st["history"][0]["latency"] == 6
    log = client.get("/api/maintenance/log").json()["lines"]
    assert "canal ocupado" in log[0]  # lo más reciente primero
    r = client.get("/api/maintenance/backup")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.content


def test_settings(client):
    assert client.put("/api/maintenance/ntp", json={"servers": []}).status_code == 400
    assert client.put("/api/maintenance/ntp", json={"servers": ["hora.roa.es", "pool.ntp.org"]}).status_code == 200
    assert client.get("/api/maintenance").json()["time"]["servers"] == ["hora.roa.es", "pool.ntp.org"]

    bad = {"enabled": True, "service": "Nope", "username": "u", "hostname": "casa.ddns.net"}
    assert client.put("/api/maintenance/dyndns", json=bad).status_code == 400
    ok = {**bad, "service": "No-IP", "password": "x"}
    assert client.put("/api/maintenance/dyndns", json=ok).status_code == 200
    d = client.get("/api/maintenance").json()["dyndns"]
    assert (d["enabled"], d["service"], d["hostname"]) == (True, "No-IP", "casa.ddns.net")

    assert client.put("/api/maintenance/remote-access", json={"ssh": True}).status_code == 200
    assert client.get("/api/maintenance").json()["remote_access"]["ssh"] is True

    assert client.put("/api/maintenance/syslog", json={"enabled": True, "ip": "nope"}).status_code == 400
    assert client.put("/api/maintenance/syslog", json={"enabled": True, "ip": "192.168.1.10", "port": 514}).status_code == 200


def test_password_change(client):
    assert client.post("/api/maintenance/password", json={"old": "1234", "new": "corta"}).status_code == 400
    assert client.post("/api/maintenance/password", json={"old": "mala", "new": "nueva-clave-1"}).status_code == 502
    assert client.post("/api/maintenance/password", json={"old": "1234", "new": "nueva-clave-1"}).status_code == 200


def test_env_update_never_touches_real_env(tmp_path, monkeypatch):
    real = ROOT / ".env"
    before = real.read_bytes() if real.exists() else None
    env = tmp_path / "x.env"
    env.write_text("SAGEMCOM_USER=1234\nSAGEMCOM_PASS=vieja\nOTRA=1\n", encoding="utf-8")
    monkeypatch.setattr(maintenance, "ENV_FILE", env)
    monkeypatch.setattr(maintenance, "settings", SimpleNamespace(mock=False, user="1234"))
    maintenance._update_env_password("nueva")
    assert env.read_text(encoding="utf-8") == "SAGEMCOM_USER=1234\nSAGEMCOM_PASS=nueva\nOTRA=1\n"
    assert (real.read_bytes() if real.exists() else None) == before


def test_factory_reset_needs_phrase(client):
    assert client.post("/api/maintenance/factory-reset", json={"confirm": "si"}).status_code == 403
    assert client.post("/api/maintenance/factory-reset", json={"confirm": "RESTABLECER"}).status_code == 200
