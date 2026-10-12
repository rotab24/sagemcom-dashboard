import json

from sagemcom.client import SagemcomClient, SessionExpired, md5, XMO_INVALID_SESSION_ERR, XMO_NO_ERR, XMO_REQUEST_NO_ERR


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def login_reply(nonce="12345", sid=777):
    return {"reply": {"error": {"code": XMO_REQUEST_NO_ERR}, "actions": [
        {"error": {"code": XMO_NO_ERR}, "callbacks": [{"parameters": {"id": sid, "nonce": nonce}}]}]}}


def value_reply(*values):
    return {"reply": {"error": {"code": XMO_REQUEST_NO_ERR}, "actions": [
        {"error": {"code": XMO_NO_ERR}, "callbacks": [{"parameters": {"value": v}}]} for v in values]}}


def make(replies):
    c = SagemcomClient("192.168.1.1", "admin", "secreto")
    sent = []

    def post(url, data, timeout):
        sent.append(json.loads(data["req"]))
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeResponse(r)

    c.s.post = post
    return c, sent


def test_auth_key_follows_gui_algorithm():
    c, sent = make([login_reply(nonce="999"), value_reply("F@st 5657")])
    assert c.get_value("Device/DeviceInfo/ModelName") == "F@st 5657"
    # login: ha1 sin nonce
    first = sent[0]["request"]
    assert set(sent[0]) == {"request"}  # cnonce/auth-key van dentro de request, como en gui-api.js
    ha1 = md5("admin::" + md5("secreto"))
    assert first["auth-key"] == md5(f"{ha1}:0:{first['cnonce']}:JSON:/cgi/json-req")
    # siguientes: ha1 con el nonce devuelto, session-id del login
    second = sent[1]["request"]
    ha1 = md5("admin:999:" + md5("secreto"))
    assert second["session-id"] == 777
    assert second["auth-key"] == md5(f"{ha1}:1:{second['cnonce']}:JSON:/cgi/json-req")


def test_get_values_batches_in_one_request():
    c, sent = make([login_reply(), value_reply("a", "b")])
    assert c.get_values({"x": "X/1", "y": "Y/2"}) == {"x": "a", "y": "b"}
    assert [a["xpath"] for a in sent[1]["request"]["actions"]] == ["X/1", "Y/2"]
    assert [a["id"] for a in sent[1]["request"]["actions"]] == [0, 1]


def test_relogin_on_expired_session():
    expired = {"reply": {"error": {"code": XMO_INVALID_SESSION_ERR, "description": "expired"}}}
    c, sent = make([login_reply(sid=1), expired, login_reply(sid=2), value_reply(42)])
    assert c.get_value("Device/DeviceInfo/UpTime") == 42
    assert sent[3]["request"]["session-id"] == 2
    assert [s["request"]["actions"][0]["method"] for s in sent] == ["logIn", "getValue", "logIn", "getValue"]


def test_no_retry_after_auth_error():
    import pytest
    from sagemcom.client import SagemcomError, XMO_AUTHENTICATION_ERR
    rejected = {"reply": {"error": {"code": XMO_REQUEST_NO_ERR}, "actions": [
        {"error": {"code": XMO_AUTHENTICATION_ERR, "description": "XMO_AUTHENTICATION_ERR"}}]}}
    c, sent = make([rejected])
    with pytest.raises(SagemcomError):
        c.get_value("Device/DeviceInfo/UpTime")
    with pytest.raises(SagemcomError, match="rechazado hace poco"):
        c.get_value("Device/DeviceInfo/UpTime")
    assert len(sent) == 1  # el segundo intento no llega al router


def test_read_retried_once_when_router_drops_connection():
    import requests
    c, sent = make([login_reply(), requests.ConnectionError("RemoteDisconnected"), value_reply(7)])
    assert c.get_value("Device/DeviceInfo/UpTime") == 7
    assert len(sent) == 3


def test_writes_not_retried_and_router_left_alone_after_failure():
    import pytest
    import requests
    from sagemcom.client import RouterUnavailable
    c, sent = make([login_reply(), requests.ConnectionError("RemoteDisconnected")])
    with pytest.raises(RouterUnavailable):
        c.set_value("Device/X", 1)
    assert len(sent) == 2  # una escritura que pudo aplicarse no se repite
    with pytest.raises(RouterUnavailable, match="se reintentará"):
        c.get_value("Device/DeviceInfo/UpTime")
    assert len(sent) == 2  # durante la pausa no se envía nada al router
