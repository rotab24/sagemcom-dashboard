import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def read(client, path):
    return client.get("/api/xpath", params={"path": path}).json()["value"]


def test_set_requires_confirmation(client):
    path = 'Device/WiFi/Radios/Radio[Alias="RADIO2G4"]/Channel'
    assert client.post("/api/expert/set", json={"path": path, "value": 6}).status_code == 403
    r = client.post("/api/expert/set", json={"path": path, "value": 6, "confirm": "yes"})
    assert r.status_code == 200 and r.json()["before"] == 1
    assert read(client, path) == 6
    ev = client.get("/api/events").json()[0]
    assert ev["kind"] == "expert" and "Channel" in ev["message"]


def test_dangerous_paths_need_phrase(client):
    path = 'Device/Optical/Interfaces/Interface[@uid="1"]/Status'
    assert client.get("/api/expert/danger", params={"path": path}).json()["danger"]
    assert client.post("/api/expert/set", json={"path": path, "value": "DOWN", "confirm": "yes"}).status_code == 403
    assert client.post("/api/expert/set", json={"path": path, "value": "UP", "confirm": "ENTIENDO EL RIESGO"}).status_code == 200


def test_add_and_delete_child(client):
    sched = 'Device/Hosts/Hosts/Host[@uid="4"]/BlacklistedSchedule'
    bad = client.post("/api/expert/add", json={"path": sched, "value": {"a": 1, "b": 2}, "confirm": "yes"})
    assert bad.status_code == 400
    day = {"ManagementDay": {"DayIndex": "EXTRA", **{f"Hours{h}": False for h in range(24)}}}
    r = client.post("/api/expert/add", json={"path": sched, "value": day, "confirm": "yes"})
    assert r.status_code == 200
    uid = r.json()["result"]["uid"]
    assert len(read(client, sched)) == 8
    assert client.post("/api/expert/delete", json={"path": f'{sched}/ManagementDay[@uid="{uid}"]', "confirm": "yes"}).status_code == 200
    assert len(read(client, sched)) == 7


def test_unknown_path_is_router_error(client):
    r = client.post("/api/expert/set", json={"path": "Device/NoExiste", "value": 1, "confirm": "yes"})
    assert r.status_code == 502
