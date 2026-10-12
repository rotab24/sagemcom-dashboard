# Sagemcom Dashboard

Dashboard local para controlar y automatizar el router **Sagemcom F@st 5657** (MásMóvil/Yoigo, GPON)
usando la API interna XMO (`POST /cgi/json-req`) que usa la propia web del router. Cada función replica
lo que hace la web oficial (sacado de `scripts.js`, `gui-api.js`, `xmo.js` y las plantillas `views/*.html`).

## Puesta en marcha

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          # usuario y contraseña del router (en el F5657 de MásMóvil: 1234 / la de la pegatina)
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Abre <http://127.0.0.1:8765>. Para probarlo sin router: `SAGEMCOM_MOCK=1` en `.env`.

### Arrancar con un clic

```bash
powershell -ExecutionPolicy Bypass -File crear-accesos.ps1            # accesos en escritorio y menú Inicio
powershell -ExecutionPolicy Bypass -File crear-accesos.ps1 -Autostart # además, arranca al iniciar sesión
```

"Router Dashboard" ejecuta `iniciar.ps1`: arranca el servidor sin ventana (si no estaba ya en marcha), espera a que
responda y abre el navegador. "Detener Router Dashboard" ejecuta `detener.ps1`. El registro queda en `data/server.err.log`.

> El servidor no tiene login propio: déjalo en `127.0.0.1`. Si lo abres a la red (`--host 0.0.0.0`),
> cualquiera en tu LAN podría cambiar la Wi‑Fi, abrir puertos o restablecer el router.
> Las automatizaciones, horarios de Wi‑Fi, bloqueos temporales y avisos solo funcionan mientras el servidor está encendido.

## Pestañas

| Pestaña | Qué hace |
|---|---|
| **Resumen** | Internet, tráfico, fibra, router, dispositivos; gráfica de tráfico; informe (consumo por día, caídas, picos, aparatos nuevos); gráfica de la señal de la fibra; eventos |
| **Dispositivos** | Tabla con MAC, IP, tipo, grupos y notas, ordenable por columna (por defecto por IP) y con filtros por columna; búsqueda en todos los campos (sin tildes, varias palabras), agrupar por tipo/grupo/conexión/estado. Selección múltiple con acciones en bloque (grupos, tipo, bloquear/desbloquear, conocidos, exportar). Menú ⋯ por fila. Exportar/importar CSV (`;`, UTF-8; al importar se ve una vista previa y los cambios de nombre/IP fija en el router son opcionales). Ficha de cada aparato: nombre, **IP fija** (reserva DHCP en el router), tipo, grupos y notas (tipo, grupos y notas solo se guardan en el dashboard). Bloquear (ya / X minutos / indefinido), horario semanal sin Internet, marcar conocidos; **grupos** para bloquear u horarios en bloque |
| **Wi‑Fi** | Encender/apagar redes, nombre, contraseña, visibilidad, QR, clientes con señal, canal; Wi‑Fi de invitados temporal; seguridad, WPS, filtro MAC, aislamiento, límite de clientes, horario de encendido, ancho de canal, potencia, band steering, escaneo de redes vecinas con canal recomendado |
| **Red y seguridad** | Abrir puertos, puertos UPnP, IPs fijas, DMZ, DHCP, DNS, firewall, UPnP |
| **Teléfono y USB** | Línea VoIP (llamada en espera, no molestar, desvíos, ocultar número…), prueba de timbre, registro de llamadas; USB, SMB y DLNA |
| **Automatizaciones** | Avisos al móvil con ntfy: varias **listas** (un tema de ntfy cada una, para que cada persona se suscriba a la suya) y en cada lista una fila por categoría con título y mensaje editables (variables como `{nombre}`, `{ip}`, `{duracion}`), prioridad, etiquetas, filtro de aparatos por grupo/tipo (todos / solo / excepto), horas de silencio y botón de prueba con resultado real. Editor de automatizaciones con cron (sin reiniciar) |
| **Herramientas** | Reiniciar el dashboard (se confirma cuando contesta un proceso nuevo), ping, traceroute, nslookup, test de velocidad, registro del router, copia de seguridad, hora (NTP), DNS dinámico, acceso remoto, syslog, contraseña del router, reiniciar, restablecer de fábrica, explorador de xpaths con **modo experto** (escribir cualquier valor) |

**Personalizar la interfaz**: botón **✎ Editar** (arriba a la derecha) o <kbd>Ctrl</kbd>+<kbd>E</kbd>. Haz clic
en cualquier texto fijo de la interfaz para cambiarlo (Enter guarda, Esc cancela, vacío = texto original;
<kbd>Alt</kbd>+clic hace el clic normal). "Apariencia" cambia los colores (por separado para modo claro y
oscuro), el tamaño del texto y la escala. Se guarda en `data/texts.json` y `data/theme.json`, y la página
se sirve ya con los cambios. Los datos que pinta el dashboard (nombres de aparatos, tablas, avisos…) no son
textos de la interfaz y se cambian en su sitio.

No implementado (hazlo desde 192.168.1.1): **restaurar** una copia de seguridad y **actualizar firmware**
(se suben como archivo a `/cgi/upload` y no se pueden probar sin riesgo).

**Cambios comprobados**: tras cada escritura en el router (setValue, addChild, deleteChild) el dashboard
vuelve a leer y compara. Si el router dijo "OK" pero el valor no ha cambiado, la acción falla con un error
claro. Las respuestas llevan la cabecera `X-Router-Verified` y la web lo muestra como
"✓ comprobado en el router". Los disparadores (lanzar un escaneo) se marcan con `verify=False`.

Detalles del firmware que el dashboard tiene en cuenta:
- La red de invitados se enciende con `AccessPoint/Enable`, no con el SSID.
- `WL_BACKHAUL` y `WL_LIGHTHOUSE` son redes internas del mesh: no se pueden tocar.
- El control parental es `Host/BlacklistedSchedule` (7 días × 24 h, `true` = sin Internet) y necesita
  `Device/Services/ParentalControl/Enable`, que se activa solo al primer bloqueo.
- El servidor web del router es lento y se satura: leer `Device/Hosts/Hosts` (≈300 KB con 70 aparatos) le cuesta
  ~10 s. El dashboard reutiliza la lista 50 s (`CACHE_TTL`), no solapa recargas, no recarga con la pestaña oculta,
  reintenta una vez las lecturas cortadas (`RemoteDisconnected`) y, si el router no responde, lo deja en paz 30 s.
- No existe horario de Wi‑Fi en el firmware (`Device/WiFi/Availability`): lo aplica el dashboard cada hora.

## Automatizaciones

Se editan en la pestaña Automatizaciones (se guardan en `automations.json`). Acciones:

| action | params |
|---|---|
| `wifi` | `alias` (p. ej. `WL_GUEST2G4`), `enabled` |
| `reboot` | — |
| `ping_watchdog` | `host`, `count`, `reboot_on_fail` |
| `block_group` / `unblock_group` | `group`, `minutes` (opcional) |
| `guest_wifi` | `hours`, `new_password`, `bands` (`["2g","5g"]`) |
| `speedtest` | — |

## Estructura

```
sagemcom/client.py     Cliente XMO: login, re-login, peticiones agrupadas, addChild/deleteChild, descargas
sagemcom/mock.py       Router simulado (árbol de datos + intérprete de xpaths)
app/router.py          Estado, fibra, hosts, Wi-Fi básica, ping, reboot
app/devices.py         Renombrar, bloqueo y horarios (control parental)
app/wifi.py            Nombre, contraseña, visibilidad, canal, QR
app/wifi_advanced.py   Seguridad, WPS, filtro MAC, radios, band steering, vecinos, horario Wi-Fi
app/network.py         Puertos, DMZ, IPs fijas, DHCP, DNS, firewall, UPnP
app/maintenance.py     Diagnóstico, velocidad, registro, copia, NTP, DynDNS, acceso remoto, contraseña, reset
app/phone_usb.py       Telefonía y USB/DLNA
app/extras.py          Vigilancia, grupos, invitados temporal, informe
app/notify.py          Avisos ntfy: listas, reglas por categoría, plantillas, filtros, silencio
app/hosts_table.py     Tabla de dispositivos: datos, acciones en bloque, CSV
app/system.py          Información del proceso y reinicio desde la web
app/ui_custom.py       Textos y apariencia personalizados (modo edición)
app/expert.py          Modo experto (setValue/addChild/deleteChild de cualquier xpath)
app/history.py         SQLite: muestras, dispositivos, eventos, ajustes, grupos, horarios
app/automations.py     APScheduler: recolector, automatizaciones, bloqueos temporales
app/main.py            FastAPI
app/static/            Frontend (HTML/JS sin dependencias, un JS por pestaña)
```

Documentación interactiva de la API en `/docs`.

## Tests

```bash
.venv\Scripts\python -m pytest
```

Usan el router simulado, una base de datos temporal y nunca tocan `.env` ni `automations.json` reales.
