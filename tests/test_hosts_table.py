import csv
import io

import pytest
from fastapi.testclient import TestClient

from app import history
from app.main import app

MAC1, MAC2, MAC3 = "a4:83:e7:11:22:33", "3c:22:fb:44:55:66", "b8:27:eb:77:88:99"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def hosts(client):
    return {h["mac"]: h for h in client.get("/api/hosts").json()}


def test_bulk_groups_type_and_known(client):
    r = client.post("/api/hosts/bulk", json={"macs": [MAC1, MAC2.upper()], "action": "add_group", "value": "Salón"})
    assert r.status_code == 200 and "2 dispositivo" in r.json()["message"]
    client.post("/api/hosts/bulk", json={"macs": [MAC2], "action": "add_group", "value": "IoT"})
    h = hosts(client)
    assert h[MAC1]["groups"] == ["Salón"] and sorted(h[MAC2]["groups"]) == ["IoT", "Salón"]
    client.post("/api/hosts/bulk", json={"macs": [MAC1, MAC2], "action": "remove_group", "value": "Salón"})
    h = hosts(client)
    assert h[MAC1]["groups"] == [] and h[MAC2]["groups"] == ["IoT"]
    client.post("/api/hosts/bulk", json={"macs": [MAC1, MAC3], "action": "set_type", "value": "IoT"})
    client.post("/api/hosts/bulk", json={"macs": [MAC1, MAC3], "action": "known"})
    h = hosts(client)
    assert h[MAC1]["type"] == h[MAC3]["type"] == "IoT" and h[MAC1]["known"] and h[MAC3]["known"]
    # Validaciones
    assert client.post("/api/hosts/bulk", json={"macs": ["zz"], "action": "known"}).status_code == 400
    assert client.post("/api/hosts/bulk", json={"macs": [MAC1], "action": "add_group"}).status_code == 400
    assert client.post("/api/hosts/bulk", json={"macs": [], "action": "known"}).status_code == 422


def read_export(client, query=""):
    r = client.get("/api/hosts/export.csv" + query)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith("﻿")
    return text, list(csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"))


def test_export_sorted_by_ip_and_filtered(client):
    _, rows = read_export(client)
    ips = [r["ip"] for r in rows if r["ip"]]
    assert ips == sorted(ips, key=lambda ip: tuple(int(p) for p in ip.split(".")))
    assert {"mac", "nombre", "ip_fija", "tipo", "grupos", "notas", "conocido"} <= set(rows[0])
    _, rows = read_export(client, f"?macs={MAC2},{MAC3}")
    assert {r["mac"] for r in rows} == {MAC2, MAC3}
    assert next(r for r in rows if r["mac"] == MAC2)["grupos"] == "IoT"


def test_export_escapes_formulas(client):
    from app.hosts_table import _cell, _safe_cell
    assert _safe_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _safe_cell("Móvil") == "Móvil"
    assert _cell("'=HYPERLINK(1)") == "=HYPERLINK(1)"


def test_unchanged_export_imports_as_no_changes(client):
    text, _ = read_export(client)
    plan = client.post("/api/hosts/import/preview", json={"csv": text}).json()
    assert plan["changes"] == [] and plan["errors"] == [] and plan["router_changes"] == 0


def test_import_preview_and_apply(client):
    text = ("MAC,Tipo,Grupos,Notas,ip_fija,Nombre\n"
            f"{MAC1.upper()},Portátil,Trabajo|Casa,Mesa del salón,192.168.1.60,\n"
            f"{MAC3},,,,,Pi-hole\n"
            "00:11:22:33:44:55,Cámara,IoT,Garaje,,Cam\n"
            "no-es-mac,x,,,,\n")
    plan = client.post("/api/hosts/import/preview", json={"csv": text}).json()
    by_mac = {c["mac"]: c["fields"] for c in plan["changes"]}
    assert by_mac[MAC1]["tipo"] == ["IoT", "Portátil"]
    assert by_mac[MAC1]["grupos"] == [[], ["Casa", "Trabajo"]]
    assert by_mac[MAC1]["ip_fija"] == ["", "192.168.1.60"]
    assert "nombre" not in by_mac[MAC1]  # vacío y sin nombre propio -> no se toca
    assert by_mac[MAC3]["nombre"] == ["raspberrypi", "Pi-hole"]
    assert by_mac[MAC3]["tipo"] == ["IoT", ""]
    assert "00:11:22:33:44:55" in by_mac  # aún no conectado: tipo/grupos/notas sí
    assert any("no-es-mac" in e for e in plan["errors"])
    assert any("Cam" in s and "nombre" in s for s in plan["skipped"])
    assert plan["router_changes"] == 2

    # Sin marcar "también el router": solo cambia lo del dashboard
    res = client.post("/api/hosts/import/apply", json={"csv": text}).json()
    h = hosts(client)
    assert h[MAC1]["type"] == "Portátil" and h[MAC1]["notes"] == "Mesa del salón"
    assert h[MAC1]["fixed_ip"] is None and h[MAC3]["name"] == "raspberrypi"
    assert history.devices()["00:11:22:33:44:55"]["type"] == "Cámara"
    assert res["applied"] > 0 and any("no-es-mac" in e for e in res["errors"])

    # Con el router: nombre e IP fija
    res = client.post("/api/hosts/import/apply", json={"csv": text, "include_router": True}).json()
    h = hosts(client)
    assert h[MAC1]["fixed_ip"] == "192.168.1.60" and h[MAC3]["name"] == "Pi-hole"
    assert res["applied"] == 2
    # El router simulado es compartido con otros tests: dejarlo como estaba
    assert client.put(f"/api/hosts/{h[MAC1]['uid']}/details", json={"fixed_ip": ""}).status_code == 200


def test_import_rejects_bad_files(client):
    assert client.post("/api/hosts/import/preview", json={"csv": ""}).status_code == 400
    r = client.post("/api/hosts/import/preview", json={"csv": "nombre;tipo\nx;y\n"})
    assert r.status_code == 400 and "mac" in r.json()["detail"]
