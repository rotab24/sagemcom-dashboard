from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import history, notify
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sent(monkeypatch):
    """Captura los avisos en lugar de mandarlos a ntfy (y los envía en el mismo hilo)."""
    calls = []

    class FakeResp:
        def raise_for_status(self):
            pass

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "headers": headers, **json})
        return FakeResp()

    class SyncThread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(notify.requests, "post", fake_post)
    monkeypatch.setattr(notify.threading, "Thread", SyncThread)
    history.set_setting("notify_lists", [])
    return calls


def make_list(client, **kw):
    body = {"name": "Yo", "enabled": True, "topic": "router-yo-x7k2p9", **kw}
    r = client.post("/api/notify/lists", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_old_config_becomes_a_list(client):
    history.set_setting("notify_lists", None)
    history.set_setting("notify", {"enabled": True, "topic": "viejo-tema-123", "server": "https://ntfy.sh",
                                   "kinds": ["internet_down"]})
    lists = client.get("/api/notify").json()["lists"]
    assert len(lists) == 1 and lists[0]["name"] == "Principal" and lists[0]["topic"] == "viejo-tema-123"
    assert lists[0]["rules"]["internet_down"]["enabled"] and not lists[0]["rules"]["new_device"]["enabled"]
    history.set_setting("notify", None)


def test_validation(client, sent):
    assert client.post("/api/notify/lists", json={"name": "x", "topic": "corto"}).status_code == 400
    assert client.post("/api/notify/lists", json={"name": "x", "enabled": True}).status_code == 400
    assert client.post("/api/notify/lists", json={"name": "x", "server": "ftp://a"}).status_code == 400
    bad_rule = {"internet_down": {"enabled": True, "priority": 9}}
    assert client.post("/api/notify/lists", json={"name": "x", "rules": bad_rule}).status_code == 400
    bad_filter = {"new_device": {"enabled": True, "filter": {"mode": "include"}}}
    assert client.post("/api/notify/lists", json={"name": "x", "rules": bad_filter}).status_code == 400
    make_list(client)
    assert client.post("/api/notify/lists", json={"name": "Otra", "topic": "router-yo-x7k2p9"}).status_code == 400


def test_templates_priority_and_token(client, sent):
    lst = make_list(client, token="tk_secreto", rules={
        "internet_up": {"enabled": True, "title": "Volvió ({hora})", "template": "Caída de {duracion}. {nada}",
                        "priority": 5, "tags": ["tada"]}})
    assert lst["has_token"] and "token" not in lst
    history.add_event("internet_up", "Internet recuperado tras 7 min", duracion="7 min")
    m = sent[-1]
    assert m["topic"] == "router-yo-x7k2p9" and m["message"] == "Caída de 7 min. {nada}"
    assert m["title"].startswith("Volvió (") and m["priority"] == 5 and m["tags"] == ["tada"]
    assert m["headers"] == {"Authorization": "Bearer tk_secreto"}
    # Guardar sin token lo conserva; "" lo quita
    client.put(f"/api/notify/lists/{lst['id']}", json={"name": "Yo", "enabled": True, "topic": "router-yo-x7k2p9"})
    assert notify.load_lists()[0]["token"] == "tk_secreto"
    client.put(f"/api/notify/lists/{lst['id']}", json={"name": "Yo", "enabled": True, "topic": "router-yo-x7k2p9", "token": ""})
    assert notify.load_lists()[0]["token"] == ""
    # Categoría desactivada o lista desactivada: nada
    n = len(sent)
    history.add_event("guest", "Wi-Fi de invitados apagada")  # "guest" viene desactivada por defecto
    client.put(f"/api/notify/lists/{lst['id']}", json={"name": "Yo", "enabled": False, "topic": "router-yo-x7k2p9"})
    history.add_event("internet_up", "otra vez", duracion="1 min")
    assert len(sent) == n


def test_device_filters_and_several_lists(client, sent):
    history.set_device_groups("aa:aa:aa:aa:aa:01", ["IoT"])
    rule = lambda f: {"new_device": {"enabled": True, "template": "{nombre} [{grupos}] {ip}", "filter": f}}  # noqa: E731
    make_list(client, name="Sin IoT", topic="lista-sin-iot-1", rules=rule({"mode": "exclude", "groups": ["IoT"]}))
    make_list(client, name="Solo móviles", topic="lista-moviles-1", rules=rule({"mode": "include", "types": ["Smartphone"]}))
    make_list(client, name="Todo", topic="lista-todo-1234", rules=rule({"mode": "all"}))

    def topics_for(mac, tipo):
        before = len(sent)
        history.add_event("new_device", "nuevo", mac=mac, nombre="Cosa", ip="192.168.1.9", tipo=tipo)
        return sorted(m["topic"] for m in sent[before:])

    assert topics_for("aa:aa:aa:aa:aa:01", "") == ["lista-todo-1234"]  # IoT: excluido de "Sin IoT"
    assert topics_for("aa:aa:aa:aa:aa:02", "Smartphone") == ["lista-moviles-1", "lista-sin-iot-1", "lista-todo-1234"]
    assert topics_for("aa:aa:aa:aa:aa:03", "PC") == ["lista-sin-iot-1", "lista-todo-1234"]
    # El tipo que pone el usuario en la ficha también cuenta
    history.set_device_meta("aa:aa:aa:aa:aa:03", type="Smartphone")
    assert "lista-moviles-1" in topics_for("aa:aa:aa:aa:aa:03", "PC")
    assert sent[-1]["message"] == "Cosa [] 192.168.1.9"


def test_quiet_hours():
    lst = notify.new_list("Q")
    lst.update(enabled=True, topic="tema-quiet-123", quiet={"enabled": True, "start": 23, "end": 8, "mode": "silent"})
    night, day = datetime(2026, 1, 1, 2, 0), datetime(2026, 1, 1, 12, 0)
    assert notify.build(lst, "internet_down", "x", {}, night)["priority"] == 1
    assert notify.build(lst, "internet_down", "x", {}, day)["priority"] == 4
    assert notify.build(lst, "intruder", "x", {"mac": ""}, night)["priority"] == 5  # urgente suena siempre
    lst["quiet"]["mode"] = "skip"
    assert notify.build(lst, "internet_down", "x", {}, night) is None


def test_send_test_reports_result(client, sent):
    lst = make_list(client, rules={"fiber": {"enabled": True, "template": "Señal {rx}", "priority": 4}})
    r = client.post(f"/api/notify/lists/{lst['id']}/test", json={"kind": "fiber"}).json()
    assert "aceptado" in r["message"] and sent[-1]["message"] == "Señal -24.1 dBm"
    assert sent[-1]["title"].startswith("[Prueba]")
    status = client.get("/api/notify").json()["lists"][0]["status"]
    assert status["ok"]
    assert client.post("/api/notify/lists/nope/test", json={}).status_code == 404
    assert client.delete(f"/api/notify/lists/{lst['id']}").status_code == 200
    assert client.get("/api/notify").json()["lists"] == []


def test_render_does_not_evaluate():
    assert notify.render("{mensaje.__class__} {mensaje}", {"mensaje": "hola"}) == "{mensaje.__class__} hola"
