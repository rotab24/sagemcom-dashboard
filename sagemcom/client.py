"""
Cliente de la API XMO de los routers Sagemcom (probado con F@st 5657, perfil
masmovil_gpon_F5657). Basado en sagemcom_cli.py, que a su vez se sacó de
gui-api.js / xmo.js del propio router.

Diferencias respecto al CLI:
  - Thread-safe (un lock por cliente): FastAPI ejecuta los endpoints síncronos
    en un pool de hilos y el router exige ids de petición consecutivos.
  - Re-login automático cuando la sesión caduca.
  - get_values(): agrupa varios getValue en una sola petición HTTP.
"""

import hashlib
import json
import logging
import random
import threading
import time

import requests

log = logging.getLogger(__name__)

# Códigos de error de la API XMO (ver $.xmo.err en xmo.js)
XMO_REQUEST_NO_ERR = 16777216
XMO_NO_ERR = 16777238
XMO_INVALID_SESSION_ERR = 16777219
XMO_INVALIDE_HOST_SESSION_ERR = 16777228
XMO_REQUEST_ID_ERR = 16777233
XMO_REQUEST_ACTION_ERR = 16777236
XMO_AUTHENTICATION_ERR = 16777223

# Tras un login rechazado no se reintenta hasta pasado este tiempo: el router
# bloquea temporalmente el usuario si recibe varios intentos fallidos seguidos.
AUTH_FAIL_COOLDOWN = 300

# Si el router no responde (saturado, reiniciando…) se deja de enviarle
# peticiones durante este tiempo en vez de encolarlas: insistir lo mantiene
# saturado (su servidor web atiende las peticiones de una en una).
UNREACHABLE_COOLDOWN = 30

_REQUEST_OK_CODES = (XMO_REQUEST_NO_ERR, XMO_NO_ERR, XMO_REQUEST_ACTION_ERR)
_ACTION_OK_CODES = (XMO_REQUEST_NO_ERR, XMO_NO_ERR)
_SESSION_ERR_CODES = (XMO_INVALID_SESSION_ERR, XMO_INVALIDE_HOST_SESSION_ERR, XMO_REQUEST_ID_ERR)


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


class SagemcomError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class SessionExpired(SagemcomError):
    pass


class RouterUnavailable(SagemcomError):
    pass


class SagemcomClient:
    def __init__(self, host: str, user: str, password: str, scheme: str = "http",
                 verify_tls: bool = True, timeout: int = 15, gui_version: str = "2.0"):
        self.base = f"{scheme}://{host}"
        self.endpoint = f"{self.base}/cgi/json-req"
        self.user = user
        # En este firmware GUI_PASSWORD_SALT == "" -> hash directo de la contraseña.
        self.password_hash = md5(password)
        self.timeout = timeout
        self._lock = threading.RLock()
        self._auth_failed_at = 0.0
        self._down_until = 0.0
        self._reset_session()
        self.s = requests.Session()
        self.s.verify = verify_tls
        # El router valida estas cabeceras; sin ellas rechaza el login.
        self.s.headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.base,
            "Referer": f"{self.base}/{gui_version}/gui/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })

    # ---- primitivas de protocolo -------------------------------------------------

    def _reset_session(self):
        self.session_id = "0"
        self.nonce = ""
        self.req_id = 0
        self.ha1 = md5(f"{self.user}::{self.password_hash}")
        self.logged_in = False

    def _send(self, actions, priority: bool = False) -> dict:
        req_id = self.req_id
        self.req_id += 1
        cnonce = random.randint(0, 4294967295)
        auth_key = md5(f"{self.ha1}:{req_id}:{cnonce}:JSON:/cgi/json-req")
        # cnonce y auth-key van DENTRO de "request" (gui-api.js:
        # c.extend(t.request, {cnonce, "auth-key"})); fuera, el router no ve la
        # firma y responde XMO_AUTHENTICATION_ERR aunque la contraseña sea buena.
        payload = {
            "request": {
                "id": req_id,
                "session-id": self.session_id,
                "priority": priority,
                "actions": actions,
                "cnonce": cnonce,
                "auth-key": auth_key,
            },
        }
        resp = self.s.post(self.endpoint, data={"req": json.dumps(payload)}, timeout=self.timeout)
        resp.raise_for_status()
        reply = resp.json().get("reply", {})
        err = reply.get("error", {})
        code = err.get("code")
        if code in _SESSION_ERR_CODES:
            raise SessionExpired(f"Sesión no válida ({code}): {err.get('description')}", code)
        if code not in _REQUEST_OK_CODES:
            raise SagemcomError(f"Error de request ({code}): {err.get('description')}", code)
        return reply

    def _login_locked(self):
        wait = self._auth_failed_at + AUTH_FAIL_COOLDOWN - time.monotonic()
        if self._auth_failed_at and wait > 0:
            raise SagemcomError(
                f"Login rechazado hace poco (usuario o contraseña incorrectos). "
                f"Revisa .env y reinicia el servidor, o espera {int(wait)} s.", XMO_AUTHENTICATION_ERR)
        self._reset_session()
        actions = [{
            "id": 0,
            "method": "logIn",
            "parameters": {
                "user": self.user,
                "persistent": "true",
                "session-options": {
                    "nss": [{"name": "gtw", "uri": "http://sagemcom.com/gateway-data"}],
                    "language": "ident",
                    "context-flags": {"get-content-name": True, "local-time": True},
                    "capability-depth": 2,
                    "capability-flags": {
                        "name": True,
                        "default-value": False,
                        "restriction": True,
                        "description": False,
                    },
                    "time-format": "ISO_8601",
                    "write-only-string": "_XMO_WRITE_ONLY_",
                    "undefined-write-only-string": "_XMO_UNDEFINED_WRITE_ONLY_",
                },
            },
        }]
        reply = self._send(actions, priority=True)
        action = reply["actions"][0]
        err = action.get("error", {})
        if err.get("code") not in _ACTION_OK_CODES:
            if err.get("code") == XMO_AUTHENTICATION_ERR:
                self._auth_failed_at = time.monotonic()
            raise SagemcomError(f"Login fallido ({err.get('code')}): {err.get('description')}",
                                err.get("code"))
        self._auth_failed_at = 0.0
        params = action["callbacks"][0]["parameters"]
        self.session_id = params["id"]
        self.nonce = params.get("nonce", "")
        # A partir de ahora ha1 se calcula con el nonce real que da el router
        self.ha1 = md5(f"{self.user}:{self.nonce}:{self.password_hash}")
        self.logged_in = True
        log.info("Login correcto en %s (session-id=%s)", self.base, self.session_id)

    def login(self):
        with self._lock:
            self._login_locked()

    def logout(self):
        with self._lock:
            if self.logged_in:
                try:
                    self._send([{"id": 0, "method": "logOut"}])
                except (SagemcomError, requests.RequestException):
                    pass
            self._reset_session()

    def request(self, actions: list[dict], priority: bool = False) -> list[dict]:
        """Envía una lista de acciones (los ids se asignan aquí) y devuelve las
        acciones de la respuesta. Hace login si hace falta y reintenta una vez
        si la sesión ha caducado."""
        actions = [dict(a, id=i) for i, a in enumerate(actions)]
        read_only = all(a["method"] == "getValue" for a in actions)
        with self._lock:
            wait = self._down_until - time.monotonic()
            if wait > 0:
                raise RouterUnavailable(f"El router no responde (saturado o reiniciándose); "
                                        f"se reintentará en {int(wait) + 1} s.")
            try:
                try:
                    return self._request_locked(actions, priority)
                except requests.ConnectionError:
                    # El router cierra a veces conexiones keep-alive inactivas sin avisar
                    # (RemoteDisconnected). Una lectura se puede repetir sin riesgo.
                    if not read_only:
                        raise
                    log.info("El router cerró la conexión; reintentando la lectura")
                    self.s.close()
                    return self._request_locked(actions, priority)
            except (requests.ConnectionError, requests.Timeout) as e:
                self._down_until = time.monotonic() + UNREACHABLE_COOLDOWN
                log.warning("El router no responde (%s); pausa de %d s", e, UNREACHABLE_COOLDOWN)
                raise RouterUnavailable(f"El router no responde (saturado o reiniciándose): {e}. "
                                        f"Se reintentará en {UNREACHABLE_COOLDOWN} s.") from e

    def _request_locked(self, actions: list[dict], priority: bool) -> list[dict]:
        if not self.logged_in:
            self._login_locked()
        try:
            return self._send(actions, priority)["actions"]
        except SessionExpired:
            log.info("Sesión caducada, volviendo a hacer login")
            self._login_locked()
            return self._send(actions, priority)["actions"]

    # ---- API de alto nivel ---------------------------------------------------

    @staticmethod
    def _check(action: dict, what: str):
        err = action.get("error", {})
        if err.get("code") not in _ACTION_OK_CODES:
            raise SagemcomError(f"{what} error ({err.get('code')}): {err.get('description')}",
                                err.get("code"))

    @staticmethod
    def _value(action: dict):
        cbs = action.get("callbacks", [])
        value = cbs[0]["parameters"].get("value") if cbs else None
        # Un nodo llega envuelto con su nombre: {"AccessPoint": {...}}. El GUI
        # lo desenvuelve igual (getValue en gui-api.js toma la 1ª propiedad).
        if isinstance(value, dict) and len(value) == 1:
            inner = next(iter(value.values()))
            if isinstance(inner, (dict, list)):
                return inner
        return value

    def get_value(self, xpath: str):
        action = self.request([{"method": "getValue", "xpath": xpath}])[0]
        self._check(action, f"getValue {xpath}")
        return self._value(action)

    def get_values(self, xpaths: dict[str, str]) -> dict:
        """Lee varios xpaths en una sola petición. Devuelve {nombre: valor};
        los que fallen (p. ej. no existen en este firmware) quedan a None."""
        names = list(xpaths)
        actions = self.request([{"method": "getValue", "xpath": xpaths[n]} for n in names])
        out = {}
        for name, action in zip(names, actions):
            code = action.get("error", {}).get("code")
            out[name] = self._value(action) if code in _ACTION_OK_CODES else None
        return out

    def set_value(self, xpath: str, value):
        self.set_values({xpath: value})

    def set_values(self, values: dict[str, object]):
        actions = self.request([
            {"method": "setValue", "xpath": xp, "parameters": {"value": v}}
            for xp, v in values.items()
        ])
        for (xp, _), action in zip(values.items(), actions):
            self._check(action, f"setValue {xp}")

    def add_child(self, parent_xpath: str, value: dict):
        """Crea un elemento en una colección, p. ej.
        add_child('.../BlacklistedSchedule', {"ManagementDay": {...}})  (addChild en gui-api.js)"""
        action = self.request([{"method": "addChild", "xpath": parent_xpath, "parameters": {"value": value}}])[0]
        self._check(action, f"addChild {parent_xpath}")
        cbs = action.get("callbacks", [])
        return cbs[0]["parameters"] if cbs else None

    def delete_child(self, xpath: str):
        action = self.request([{"method": "deleteChild", "xpath": xpath}])[0]
        self._check(action, f"deleteChild {xpath}")

    def remote_call(self, xpath: str, method: str, parameters=None):
        action = self.request([{"method": method, "xpath": xpath, "parameters": parameters or {}}])[0]
        self._check(action, method)
        cbs = action.get("callbacks", [])
        return cbs[0]["parameters"] if cbs else None

    def reboot(self):
        # Mismo xpath que usa el GUI ($.xpaths.trafficStats.rpc)
        return self.remote_call("Device", "reboot", {"source": "GUI"})

    def download(self, uri: str) -> bytes:
        """Descarga un archivo cuya ruta ha generado el router (getDownloadURI,
        getVendorLogDownloadURI…). El GUI hace un GET normal a esa ruta."""
        url = uri if uri.startswith("http") else f"{self.base}/{uri.lstrip('/')}"
        with self._lock:
            if not self.logged_in:
                self._login_locked()
            # Misma cookie "session" que guarda el GUI (saveToCookie en gui-api.js)
            cookie = {
                "req_id": self.req_id, "sess_id": self.session_id, "basic": False, "user": self.user,
                "dataModel": {"name": "gtw", "nss": [{"name": "gtw", "uri": "http://sagemcom.com/gateway-data"}]},
                "ha1": self.ha1[:10] + self.password_hash + self.ha1[10:], "nonce": self.nonce,
            }
            resp = self.s.get(url, timeout=self.timeout * 2, cookies={"session": json.dumps(cookie)})
        resp.raise_for_status()
        return resp.content

    def set_password(self, password: str):
        """Tras cambiar la contraseña del router, la sesión sigue con la nueva."""
        with self._lock:
            self.password_hash = md5(password)
            self._login_locked()
