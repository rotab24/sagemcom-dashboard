import pytest
from fastapi.testclient import TestClient

from app import router
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def host(client, uid):
    return next(h for h in client.get("/api/hosts").json() if h["uid"] == uid)


def details(client, uid, **body):
    return client.put(f"/api/hosts/{uid}/details", json=body)


def test_hosts_have_new_columns(client):
    h = host(client, 1)
    assert h["mac"] == "a4:83:e7:11:22:33"
    assert h["type"] == h["detected_type"] == "PC" and h["user_type"] == ""
    assert h["notes"] == "" and h["groups"] == [] and h["fixed_ip"] is None


def test_type_notes_and_groups_only_touch_the_dashboard(client):
    router.invalidate_cache()
    r = details(client, 1, type="Portátil", notes="Trabajo\nNo apagar", groups=["IoT", "Casa"])
    assert r.status_code == 200, r.text
    h = host(client, 1)
    assert h["type"] == "Portátil" and h["detected_type"] == "PC"
    assert h["notes"] == "Trabajo\nNo apagar"
    assert sorted(h["groups"]) == ["Casa", "IoT"]
    groups = {g["name"]: g["macs"] for g in client.get("/api/extras/groups").json()}
    assert groups["IoT"] == ["a4:83:e7:11:22:33"]
    # Quitarlo de todos sus grupos borra los que se quedan vacíos; tipo vacío = el detectado
    assert details(client, 1, type="", groups=[]).status_code == 200
    h = host(client, 1)
    assert h["type"] == "PC" and h["groups"] == []
    assert "IoT" not in {g["name"] for g in client.get("/api/extras/groups").json()}


def test_fixed_ip_lifecycle(client):
    # Dejar fija la IP actual
    assert details(client, 2, fixed_ip="192.168.1.40").status_code == 200
    assert host(client, 2)["fixed_ip"] == "192.168.1.40"
    res = client.get("/api/network").json()["reservations"]
    assert [(r["mac"], r["ip"]) for r in res] == [("3c:22:fb:44:55:66", "192.168.1.40")]
    # Cambiarla actualiza la misma reserva (no crea otra)
    assert details(client, 2, fixed_ip="192.168.1.41").status_code == 200
    res = client.get("/api/network").json()["reservations"]
    assert [(r["mac"], r["ip"]) for r in res] == [("3c:22:fb:44:55:66", "192.168.1.41")]
    # Una IP reservada para otro, en uso por otro aparato o fuera de la red -> error y nada cambia
    assert details(client, 1, fixed_ip="192.168.1.41").status_code == 400
    r = details(client, 1, fixed_ip="192.168.1.10")
    assert r.status_code == 400 and "raspberrypi" in r.json()["detail"]
    assert details(client, 1, fixed_ip="10.0.0.5").status_code == 400
    assert details(client, 1, fixed_ip="192.168.1.1").status_code == 400
    assert host(client, 1)["fixed_ip"] is None
    # Quitarla
    assert details(client, 2, fixed_ip="").status_code == 200
    assert host(client, 2)["fixed_ip"] is None
    assert client.get("/api/network").json()["reservations"] == []


def test_rename_through_details(client):
    assert details(client, 3, name="Pi-hole").status_code == 200
    assert host(client, 3)["name"] == "Pi-hole"
    assert details(client, 3, name="x" * 65).status_code == 422
    assert details(client, 999, notes="hola").status_code == 404
