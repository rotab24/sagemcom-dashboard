"""
Gestión de la Wi-Fi: nombre, contraseña, visibilidad, canal, QR y clientes.
Xpaths de $.xpaths.wifi.general y saveWizard (scripts.js).
"""

import io

import segno

from .router import INTERNAL_SSIDS, _ref_alias, as_list, field, get_client, host_name, to_bool, to_num

BANDS = {"RADIO2G4": "2,4 GHz", "RADIO5G": "5 GHz", "RADIO6G": "6 GHz"}


def _ssid(alias: str) -> str:
    return f'Device/WiFi/SSIDs/SSID[Alias="{alias}"]'


def _ap(alias: str) -> str:
    return f'Device/WiFi/AccessPoints/AccessPoint[Alias="{alias}"]'


def _radio(alias: str) -> str:
    return f'Device/WiFi/Radios/Radio[Alias="{alias}"]'


def details() -> dict:
    client = get_client()
    names = {}
    for h in as_list(client.get_value("Device/Hosts/Hosts")):
        if isinstance(h, dict):
            mac = (field(h, "PhysAddress", default="") or "").lower()
            names[mac] = host_name(h, mac)

    aps = {}
    for a in as_list(client.get_value("Device/WiFi/AccessPoints")):
        if isinstance(a, dict):
            aps[_ref_alias(field(a, "SSIDReference", default=""))] = a

    networks = []
    for s in as_list(client.get_value("Device/WiFi/SSIDs")):
        if not isinstance(s, dict):
            continue
        alias = field(s, "Alias")
        if alias in INTERNAL_SSIDS:
            continue
        ap = aps.get(alias, {})
        sec = field(ap, "Security", default={}) or {}
        radio = _ref_alias(field(s, "LowerLayers", default=""))
        clients = []
        for c in as_list(field(ap, "AssociatedDevices", default=[])):
            if not isinstance(c, dict):
                continue
            mac = (field(c, "MACAddress", default="") or "").lower()
            clients.append({
                "mac": mac,
                "name": names.get(mac, mac),
                "ip": field(c, "IPAddress", default=""),
                "signal_dbm": to_num(field(c, "SignalStrength")),
                "down_mbps": round((to_num(field(c, "LastDataDownlinkRate")) or 0) / 1000, 1),
                "up_mbps": round((to_num(field(c, "LastDataUplinkRate")) or 0) / 1000, 1),
                "standard": field(c, "OperatingStandard", default=""),
                "active": to_bool(field(c, "Active", default=False)),
            })
        clients.sort(key=lambda c: (not c["active"], -(c["signal_dbm"] or -100)))
        networks.append({
            "alias": alias,
            "ssid": field(s, "SSID", default=""),
            "band": BANDS.get(radio, radio),
            "radio": radio,
            "guest": "GUEST" in alias,
            "enabled": to_bool(field(s, "Enable", default=False)) and to_bool(field(ap, "Enable", default=True)),
            "access_point": field(ap, "Alias"),
            "visible": to_bool(field(ap, "SSIDAdvertisementEnabled", default=True)),
            "security": field(sec, "ModeEnabled", default=""),
            "password": field(sec, "KeyPassphrase", default=""),
            "clients": clients,
        })

    radios = []
    for r in as_list(client.get_value("Device/WiFi/Radios")):
        if not isinstance(r, dict):
            continue
        alias = field(r, "Alias")
        radios.append({
            "alias": alias,
            "band": BANDS.get(alias, alias),
            "enabled": to_bool(field(r, "Enable", default=False)),
            "channel": int(to_num(field(r, "Channel")) or 0) or None,
            "auto_channel": to_bool(field(r, "AutoChannelEnable", default=False)),
            "possible_channels": [int(c) for c in str(field(r, "PossibleChannels", default="")).split(",") if c.strip().isdigit()],
            "bandwidth": field(r, "OperatingChannelBandwidth", default=""),
            "standards": field(r, "OperatingStandards", default=""),
        })
    return {"networks": networks, "radios": radios}


def _network(alias: str) -> dict:
    for n in details()["networks"]:
        if n["alias"] == alias:
            return n
    raise LookupError(f"No existe la red {alias}")


def update_network(alias: str, ssid: str | None = None, password: str | None = None,
                   visible: bool | None = None):
    if alias in INTERNAL_SSIDS:
        raise ValueError(f"{alias} es una red interna del mesh")
    net = _network(alias)
    values = {}
    if ssid is not None:
        if not 1 <= len(ssid.encode("utf-8")) <= 32:
            raise ValueError("El nombre de la red debe tener entre 1 y 32 caracteres")
        values[f"{_ssid(alias)}/SSID"] = ssid
    if password is not None:
        if not 8 <= len(password) <= 63 or not all(32 <= ord(c) < 127 for c in password):
            raise ValueError("La contraseña debe tener entre 8 y 63 caracteres, sin tildes ni ñ")
        values[f"{_ap(net['access_point'])}/Security/KeyPassphrase"] = password
    if visible is not None:
        values[f"{_ap(net['access_point'])}/SSIDAdvertisementEnabled"] = visible
    if values:
        get_client().set_values(values)


def set_channel(radio: str, channel: int | None):
    """channel=None -> canal automático."""
    r = next((r for r in details()["radios"] if r["alias"] == radio), None)
    if r is None:
        raise LookupError(f"No existe la radio {radio}")
    if channel is None:
        get_client().set_values({f"{_radio(radio)}/AutoChannelEnable": True})
        return
    if channel not in r["possible_channels"]:
        raise ValueError(f"Canal {channel} no válido para {r['band']}: {r['possible_channels']}")
    get_client().set_values({f"{_radio(radio)}/AutoChannelEnable": False, f"{_radio(radio)}/Channel": channel})


def _qr_escape(s: str) -> str:
    for ch in "\\;,:\"":
        s = s.replace(ch, "\\" + ch)
    return s


def qr_svg(alias: str) -> str:
    """QR estándar de Wi-Fi: al escanearlo con la cámara del móvil se conecta solo."""
    net = _network(alias)
    kind = "nopass" if not net["password"] or net["security"].upper() in ("NONE", "") else "WPA"
    payload = f"WIFI:T:{kind};S:{_qr_escape(net['ssid'])};"
    if kind == "WPA":
        payload += f"P:{_qr_escape(net['password'])};"
    if not net["visible"]:
        payload += "H:true;"
    payload += ";"
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111", light="#fff")
    return buf.getvalue().decode("utf-8")
