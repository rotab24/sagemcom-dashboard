"""
Dispositivos: nombre y control parental (bloqueo y horarios).

Cómo lo hace la web del router (ParentalControllerPlanning en scripts.js, perfil
F5657 = modo "host" con parentalControlNewFeatures):
  - Interruptor general: Device/Services/ParentalControl/Enable
    + modo lista negra: Device/Hosts/MasterBlacklistEnable = true
  - Cada Host tiene BlacklistedSchedule con un ManagementDay por día
    (DayIndex = MONDAY..SUNDAY) y casillas Hours0..Hours23.
    true = acceso DENEGADO esa hora; false = permitido.
  - "Deny All" pone todas las casillas a true.
"""

from .router import DAYS, as_list, field, get_client, grid_from_schedule, to_bool

PC_ENABLE = "Device/Services/ParentalControl/Enable"
PC_BLACKLIST_MODE = "Device/Hosts/MasterBlacklistEnable"


def _host(uid: int) -> str:
    return f'Device/Hosts/Hosts/Host[@uid="{int(uid)}"]'


def parental_status() -> dict:
    v = get_client().get_values({"enabled": PC_ENABLE, "blacklist_mode": PC_BLACKLIST_MODE})
    return {"enabled": to_bool(v["enabled"]), "blacklist_mode": to_bool(v["blacklist_mode"])}


def _ensure_parental_enabled() -> dict:
    """Los horarios solo se aplican con el control parental activo y en modo
    lista negra (en modo lista blanca se bloquearía todo lo que no esté en la
    lista, así que nunca lo dejamos en ese modo)."""
    st = parental_status()
    values = {}
    if not st["enabled"]:
        values[PC_ENABLE] = True
    if not st["blacklist_mode"]:
        values[PC_BLACKLIST_MODE] = True
    return values


def get_schedule(uid: int) -> dict[str, list[bool]]:
    return grid_from_schedule(get_client().get_value(f"{_host(uid)}/BlacklistedSchedule"))


def set_schedule(uid: int, grid: dict[str, list[bool]]):
    """Guarda el horario semanal completo de un dispositivo."""
    for d in DAYS:
        if d not in grid or len(grid[d]) != 24:
            raise ValueError(f"Horario incompleto: falta {d} o no tiene 24 horas")
    client = get_client()
    current = {field(day, "DayIndex"): day
               for day in as_list(client.get_value(f"{_host(uid)}/BlacklistedSchedule"))
               if isinstance(day, dict)}
    values = _ensure_parental_enabled()
    missing = []
    for d in DAYS:
        day = current.get(d)
        if day is None:
            missing.append(d)
            continue
        for h in range(24):
            want = bool(grid[d][h])
            if to_bool(field(day, f"Hours{h}", default=False)) != want:
                values[f'{_host(uid)}/BlacklistedSchedule/ManagementDay[@uid="{field(day, "uid")}"]/Hours{h}'] = want
    if values:
        client.set_values(values)
    for d in missing:
        day = {f"Hours{h}": bool(grid[d][h]) for h in range(24)}
        day["DayIndex"] = d
        client.add_child(f"{_host(uid)}/BlacklistedSchedule", {"ManagementDay": day})


def set_blocked(uid: int, blocked: bool):
    """Bloqueo total (equivale a "Deny All" de la web) o desbloqueo total."""
    set_schedule(uid, {d: [blocked] * 24 for d in DAYS})


def rename(uid: int, name: str, mac: str):
    # Igual que la web (ethernetDevice.save / USBDeviceInfo.save). Sin nombre,
    # el router usa la MAC, que es su valor por defecto.
    name = name.strip()
    if len(name) > 64:
        raise ValueError("El nombre no puede tener más de 64 caracteres")
    get_client().set_value(f"{_host(uid)}/UserFriendlyName", name or mac)
