import json

import pytest
from fastapi.testclient import TestClient

from app import ui_custom
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.delete("/api/ui/texts")
        c.delete("/api/ui/theme")
        yield c
        c.delete("/api/ui/texts")
        c.delete("/api/ui/theme")


def injected(client):
    html = client.get("/").text
    start = html.index("window.UI_CUSTOM = ") + len("window.UI_CUSTOM = ")
    return html, json.loads(html[start:html.index(";</script>", start)])


def test_texts_saved_and_injected(client):
    key = "tab-wifi|Wi‑Fi para invitados (temporal)"
    assert client.put("/api/ui/text", json={"key": key, "value": "  Wi-Fi   visitas  "}).status_code == 200
    assert client.get("/api/ui").json()["texts"] == {key: "Wi-Fi visitas"}
    html, data = injected(client)
    assert data["texts"][key] == "Wi-Fi visitas"
    assert html.index('id="ui-theme"') < html.index("</head>")
    # Vacío = volver al original
    client.put("/api/ui/text", json={"key": key, "value": ""})
    assert client.get("/api/ui").json()["texts"] == {}
    assert ui_custom._path("texts.json").exists()


def test_text_cannot_break_out_of_script(client):
    client.put("/api/ui/text", json={"key": "page|x", "value": "</script><script>alert(1)</script>"})
    html, data = injected(client)
    assert "</script><script>alert(1)" not in html and data["texts"]["page|x"].startswith("</script>")


def test_text_validation(client):
    assert client.put("/api/ui/text", json={"key": "sin-separador", "value": "x"}).status_code == 400
    assert client.put("/api/ui/text", json={"key": "a|b", "value": "x" * 2001}).status_code == 422


def test_theme(client):
    body = {"light": {"--accent": "#FF0000"}, "dark": {"--bg": "#000000"}, "font_size": 16, "zoom": 1.1}
    r = client.put("/api/ui/theme", json=body)
    assert r.status_code == 200 and r.json()["light"] == {"--accent": "#ff0000"}
    css = ui_custom.theme_css(ui_custom.theme())
    assert "@media not all and (prefers-color-scheme: dark){:root{--accent:#ff0000}}" in css
    assert "@media (prefers-color-scheme: dark){:root{--bg:#000000}}" in css
    assert ":root{--font-size:16px}" in css and "body{zoom:1.1}" in css
    html, _ = injected(client)
    assert "--accent:#ff0000" in html
    # Solo variables conocidas y colores #rrggbb (nada de CSS arbitrario)
    assert client.put("/api/ui/theme", json={"light": {"--accent": "red;}body{display:none"}}).status_code == 400
    assert client.put("/api/ui/theme", json={"light": {"color": "#000000"}}).status_code == 400
    assert client.put("/api/ui/theme", json={"font_size": 40}).status_code == 400
    assert client.delete("/api/ui/theme").status_code == 200
    assert ui_custom.theme_css(ui_custom.theme()) == ""
