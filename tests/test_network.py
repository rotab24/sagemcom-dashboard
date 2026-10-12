import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.router import get_client


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def net(client):
    return client.get("/api/network").json()


def test_overview(client):
    n = net(client)
    assert n["lan"] == {"ip": "192.168.1.1", "mask": "255.255.255.0", "network": "192.168.1.0/24"}
    assert n["dhcp"]["start"] == "192.168.1.128" and n["dhcp"]["lease_time"] == 86400
    assert n["dmz"] == {"exists": True, "enabled": False, "ip": ""}
    assert n["firewall"]["level"] == "LOW"
    # Las reglas UPnP y la DMZ oculta no se mezclan con las del usuario
    assert n["port_forwards"] == [] and n["upnp_rules"][0]["external_port"] == 3074


def test_port_forward_lifecycle(client):
    body = {"description": "Minecraft", "protocol": "TCP", "external_port": 25565, "internal_client": "192.168.1.10"}
    assert client.post("/api/network/portforwards", json=body).status_code == 200
    pf = net(client)["port_forwards"][0]
    assert pf["alias"] == "PORTFW_RULE_1" and pf["internal_port"] == 25565 and pf["enabled"]
    # Mismo puerto y protocolo -> conflicto; con UPnP también
    assert client.post("/api/network/portforwards", json=body).status_code == 400
    assert client.post("/api/network/portforwards", json={**body, "protocol": "UDP", "external_port": 3074}).status_code == 400
    # Validaciones
    assert client.post("/api/network/portforwards", json={**body, "external_port": 70000}).status_code == 400
    assert client.post("/api/network/portforwards", json={**body, "external_port": 8080, "internal_client": "10.0.0.5"}).status_code == 400
    assert client.post("/api/network/portforwards", json={**body, "external_port": 8080, "internal_client": "192.168.1.1"}).status_code == 400
    assert client.post("/api/network/portforwards", json={**body, "external_port": 8080, "protocol": "XYZ"}).status_code == 400
    # Editar (puede conservar su propio puerto) y borrar
    upd = {**body, "internal_port": 25566, "enabled": False}
    assert client.put(f"/api/network/portforwards/{pf['uid']}", json=upd).status_code == 200
    pf2 = net(client)["port_forwards"][0]
    assert pf2["internal_port"] == 25566 and not pf2["enabled"]
    assert client.delete(f"/api/network/portforwards/{pf['uid']}").status_code == 200
    assert net(client)["port_forwards"] == []
    assert client.delete("/api/network/portforwards/999").status_code == 404


def test_dmz(client):
    assert client.put("/api/network/dmz", json={"enabled": True, "ip": "8.8.8.8"}).status_code == 400
    assert client.put("/api/network/dmz", json={"enabled": True, "ip": "192.168.1.34"}).status_code == 200
    assert net(client)["dmz"] == {"exists": True, "enabled": True, "ip": "192.168.1.34"}
    client.put("/api/network/dmz", json={"enabled": False})
    assert net(client)["dmz"]["enabled"] is False


def test_reservations(client):
    body = {"mac": "B8-27-EB-77-88-99", "ip": "192.168.1.10"}
    assert client.post("/api/network/reservations", json=body).status_code == 200
    raw = get_client().get_value('Device/DHCPv4/Server/Pools/Pool[Alias="DEFAULT_POOL"]/StaticAddresses')
    assert raw[0]["Chaddr"] == "B8:27:EB:77:88:99" and raw[0]["Yiaddr"] == "192.168.1.10"
    r = net(client)["reservations"][0]
    assert r["mac"] == "b8:27:eb:77:88:99"
    assert client.post("/api/network/reservations", json=body).status_code == 400  # duplicada
    assert client.post("/api/network/reservations", json={"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.10"}).status_code == 400
    assert client.post("/api/network/reservations", json={"mac": "nope", "ip": "192.168.1.11"}).status_code == 400
    assert client.delete(f"/api/network/reservations/{r['alias']}").status_code == 200
    assert net(client)["reservations"] == []


def test_dhcp_and_dns(client):
    assert client.put("/api/network/dhcp", json={"start": "192.168.1.200", "end": "192.168.1.100", "lease_time": 3600}).status_code == 400
    assert client.put("/api/network/dhcp", json={"start": "192.168.1.100", "end": "192.168.1.200", "lease_time": 10}).status_code == 400
    assert client.put("/api/network/dhcp", json={"start": "192.168.1.100", "end": "192.168.1.200", "lease_time": 7200}).status_code == 200
    d = net(client)["dhcp"]
    assert (d["start"], d["end"], d["lease_time"]) == ("192.168.1.100", "192.168.1.200", 7200)
    assert client.put("/api/network/dns", json={"servers": ["1.1.1.1", "nope"]}).status_code == 400
    assert client.put("/api/network/dns", json={"servers": []}).status_code == 400
    assert client.put("/api/network/dns", json={"servers": ["1.1.1.1", "1.0.0.1"]}).status_code == 200
    assert net(client)["dhcp"]["dns_servers"] == ["1.1.1.1", "1.0.0.1"]


def test_firewall_and_upnp(client):
    assert client.put("/api/network/firewall", json={"level": "OFF"}).status_code == 400
    assert client.put("/api/network/firewall", json={"level": "high", "respond_to_ping": False}).status_code == 200
    fw = net(client)["firewall"]
    assert fw["level"] == "HIGH" and fw["respond_to_ping"] is False
    assert client.put("/api/network/upnp", json={"enabled": False}).status_code == 200
    assert net(client)["upnp"]["enabled"] is False
