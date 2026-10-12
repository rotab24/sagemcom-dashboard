"""
Router simulado con la misma interfaz que SagemcomClient (SAGEMCOM_MOCK=1).

Guarda un árbol de datos con la forma real del F@st 5657 y entiende los
xpaths que usa el dashboard: Device/Hosts/Hosts/Host[@uid="1"]/Blacklisted,
Device/WiFi/SSIDs/SSID[Alias="WL_PRIV"]/SSID, .../@uid, etc.
"""

import copy
import random
import re
import threading
import time

from .client import SagemcomError

XMO_UNKNOWN_PATH_ERR = 16777242
_START = time.time()
_DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def _schedule():
    return [dict({f"Hours{h}": False for h in range(24)}, uid=i + 1, DayIndex=d) for i, d in enumerate(_DAYS)]


def _host(uid, mac, ip, hostname, active, iface, source="DHCP", dtype="", friendly=None):
    return {
        "uid": uid, "PhysAddress": mac, "IPAddress": ip, "HostName": hostname,
        "UserFriendlyName": friendly if friendly is not None else mac, "UserHostName": "",
        "Active": active, "InterfaceType": iface, "AddressSource": source,
        "DetectedDeviceType": dtype, "BlacklistedSchedule": _schedule(),
    }


def _ap(uid, alias, ssid_alias, enable, password, clients=()):
    return {
        "uid": uid, "Alias": alias, "Enable": enable, "SSIDAdvertisementEnabled": True,
        "SSIDReference": f"Device/WiFi/SSIDs/SSID[{ssid_alias}]",
        "Security": {"ModeEnabled": "WPA2_PERSONAL", "ModesSupported": "None,WPA2-Personal,WPA-WPA2-Personal",
                     "KeyPassphrase": password},
        "WPS": {"Enable": True, "SessionStatus": "READY"},
        "MACFiltering": {"Mode": "DISABLED", "MACAddresses": []},
        "IsolationEnable": False, "MaxAssociatedDevices": 0,
        "AssociatedDevices": [
            {"uid": i + 1, "MACAddress": mac, "IPAddress": ip, "SignalStrength": sig, "Active": True,
             "LastDataDownlinkRate": rate, "LastDataUplinkRate": rate // 2, "OperatingStandard": std}
            for i, (mac, ip, sig, rate, std) in enumerate(clients)
        ],
    }


def _initial_tree():
    return {"Device": {
        "DeviceInfo": {
            "ModelName": "Fast5657_GPON", "ProductClass": "FAST5657", "SoftwareVersion": "SGDV10000069 (simulado)",
            "HardwareVersion": "1.00", "UpTime": 0, "MemoryStatus": {"Total": 473268, "Free": 171688},
            "ExternalFirmwareVersion": "SGDV10000069", "InternalFirmwareVersion": "v2.35.0",
            "BuildDate": "2023-12-11T04:23:01+0100", "RebootCount": 4, "FirstUseDate": "2026-09-27T13:44:04+0200",
            "SerialNumber": "SIMULADO0001", "MACAddress": "10:D7:B0:00:00:01",
            "Logging": {"Syslog": {"Server": {"Enable": False, "IPv4Address": "", "Port": 514}}},
            "VendorLogFiles": [{"uid": 1, "Alias": "VendorLog"}],
            "VendorConfigFiles": [{"uid": 1, "Alias": "DEVICE_CONFIG", "Name": "device.cfg"}],
        },
        "Time": {"Enable": True, "Status": "SYNCHRONIZED", "NTPServer1": "212.230.255.2", "NTPServer2": "hora.rediris.es",
                 "NTPServer3": "", "NTPServer4": "", "NTPServer5": "", "CurrentLocalTime": "2026-10-08T15:33:22+0200",
                 "LocalTimeZoneName": "Europe_Madrid"},
        "IP": {"Interfaces": [
            {"uid": 3, "Alias": "IP_DATA", "Status": "UP",
             "IPv4Addresses": [{"uid": 1, "Alias": "IP_DATA_ADDRESS", "IPAddress": "85.53.12.34",
                                "Dns": "212.230.135.1,212.230.135.2"}],
             "Stats": {"BytesReceived": 10_000_000_000, "BytesSent": 1_500_000_000}},
            {"uid": 1, "Alias": "IP_BR_LAN", "Status": "UP",
             "IPv4Addresses": [{"uid": 1, "Alias": "IP_BR_LAN_ADDRESS", "IPAddress": "192.168.1.1",
                                "SubnetMask": "255.255.255.0", "AddressingType": "STATIC"}]},
        ]},
        "NAT": {"PortMappings": [
            {"uid": 1, "Enable": False, "Status": "DISABLED", "Alias": "API_DMZ", "Service": "DMZ", "Protocol": "ALL",
             "InternalClient": "", "ExternalPort": 0, "ExternalPortEndRange": 0, "InternalPort": 0,
             "Creator": "HIDDEN", "Description": "", "RemoteHost": ""},
            {"uid": 2, "Enable": True, "Status": "ENABLED", "Alias": "UPNP_1", "Service": "", "Protocol": "UDP",
             "InternalClient": "192.168.1.34", "ExternalPort": 3074, "ExternalPortEndRange": 0, "InternalPort": 3074,
             "Creator": "UPNP", "Description": "Xbox", "RemoteHost": ""},
        ]},
        "DHCPv4": {"Server": {"Enable": True, "Pools": [{
            "uid": 1, "Alias": "DEFAULT_POOL", "Enable": True, "MinAddress": "192.168.1.128",
            "MaxAddress": "192.168.1.254", "SubnetMask": "255.255.255.0", "LeaseTime": 86400,
            "IPRouters": "192.168.1.1", "DNSServers": "212.230.135.1,212.230.135.2", "DomainName": "home",
            "StaticAddresses": [], "Clients": [],
        }]}},
        "Firewall": {"Enable": True, "Config": "LOW", "RespondToPing": True, "PortScanDetection": False,
                     "blockFragmentedIPPackets": False},
        "UPnP": {"Device": {"Enable": True, "UPnPIGD": True, "UPnPMediaServer": True},
                 "Settings": {"UPnPMediaServer": {"FriendlyName": "Fast5657"}}},
        "USB": {"Enable": True,
                "Ports": [{"uid": 1, "SysfsId": "1-1", "Name": "USB1", "Standard": "2.0", "Power": "Self"}],
                "USBHosts": {"Hosts": [{"uid": 1, "Devices": [
                    {"uid": 1, "Manufacturer": "SanDisk", "ProductClass": "Cruzer", "DeviceClass": "MassStorage",
                     "Port": "1-1", "Rate": "High"}]}]}},
        "Optical": {
            "Interfaces": [{"uid": 1, "Status": "UP", "OpticalSignalLevel": -19500, "TransmitOpticalLevel": 2640,
                            "Temperature": 38183, "Voltage": 3184800, "BIASCurrent": 12992}],
            "G988": {"GponState": "OPERATIONAL"},
        },
        "Hosts": {
            "MasterBlacklistEnable": True,
            "Hosts": [
                _host(1, "a4:83:e7:11:22:33", "192.168.1.34", "MacBook-Pro", True, "WiFi", dtype="PC"),
                _host(2, "3c:22:fb:44:55:66", "192.168.1.40", "iPhone", True, "WiFi", dtype="Smartphone",
                      friendly="iPhone de casa"),
                _host(3, "b8:27:eb:77:88:99", "192.168.1.10", "raspberrypi", True, "Ethernet", source="STATIC"),
                _host(4, "d8:3a:dd:aa:bb:cc", "192.168.1.52", "", False, "WiFi"),
            ],
        },
        "Services": {
            "ParentalControl": {"Enable": False},
            "DynamicDNS": {
                "Clients": [{"uid": 1, "Enable": False, "Status": "DISABLED", "LastError": "NOERROR",
                             "ServiceReference": "Device/Services/DynamicDNS/Services/Service[Name='DynDNS']",
                             "Username": "", "Password": "",
                             "Hostnames": [{"uid": 1, "Name": "", "LastIP": "0.0.0.0", "LastUpdate": ""}]}],
                "Services": [{"uid": 1, "Name": "DynDNS"}, {"uid": 2, "Name": "No-IP"}],
            },
            "VoiceServices": [{
                "uid": 1, "Enable": True,
                "VoiceProfiles": [{"uid": 1, "Lines": [{
                    "uid": 1, "Enable": "ENABLED", "Status": "UP", "StatusReason": "REGISTERED", "Name": "Line1",
                    "DirectoryNumber": "", "CallState": "IDLE",
                    "CallingFeatures": {"CallWaitingEnable": True, "CallerIDEnable": True, "DoNotDisturbEnable": False, "AnonymousCallBlockEnable": False, "PermanentCLIREnable": False, "CallForwardUnconditionalEnable": False, "CallForwardOnBusyEnable": False, "CallForwardOnNoAnswerEnable": False,
                                        "CallForwardUnconditionalNumber": "", "CallForwardOnBusyNumber": "",
                                        "CallForwardOnNoAnswerNumber": ""},
                }]}],
                "CallControl": {"CallLogs": [
                    {"uid": 1, "Direction": "Incoming", "RemoteParty": "600111222", "Start": "2026-10-08T10:15:00", "Duration": 125},
                    {"uid": 2, "Direction": "Outgoing", "RemoteParty": "911223344", "Start": "2026-10-08T12:40:00", "Duration": 42},
                ]},
                "PhyInterfaces": [{"uid": 1, "PhyInterfaceType": "FXS", "Description": "fxs2"}],
            }],
            "StorageServices": [{
                "uid": 1, "NetworkServer": {"SMBEnable": True}, "SMBConf": {"WorkGroup": "WORKGROUP"},
                "LogicalVolumes": [{"uid": 1, "Name": "PENDRIVE", "Status": "ONLINE", "FileSystem": "FAT32",
                                    "Capacity": 30000, "UsedSpace": 12000, "Enable": True}],
            }],
            "RemoteAccess": {"HTTPEnable": True, "HTTPSEnable": True, "TELNETEnable": False, "SSHEnable": False,
                             "TrustedDomains": [{"uid": 1, "Enable": True, "IPAddress": "212.166.64.0", "PrefixLength": 24}]},
        },
        "UserAccounts": {"Users": [{"uid": 1, "Login": "1234"}]},
        "WiFi": {
            "SSIDs": [
                {"uid": 1, "Alias": "WL_PRIV", "SSID": "MiFibra 2.4G", "Enable": True, "Status": "UP",
                 "LowerLayers": "Device/WiFi/Radios/Radio[RADIO2G4]"},
                {"uid": 2, "Alias": "WL_VIDEO_5G", "SSID": "MiFibra", "Enable": True, "Status": "UP",
                 "LowerLayers": "Device/WiFi/Radios/Radio[RADIO5G]"},
                {"uid": 3, "Alias": "WL_GUEST2G4", "SSID": "GUEST_abcd", "Enable": True, "Status": "LOWERLAYERDOWN",
                 "LowerLayers": "Device/WiFi/Radios/Radio[RADIO2G4]"},
                {"uid": 4, "Alias": "WL_GUEST5G", "SSID": "GUEST_abcd", "Enable": True, "Status": "LOWERLAYERDOWN",
                 "LowerLayers": "Device/WiFi/Radios/Radio[RADIO5G]"},
                {"uid": 5, "Alias": "WL_BACKHAUL", "SSID": "MM_BACKHAUL", "Enable": True, "Status": "LOWERLAYERDOWN",
                 "LowerLayers": "Device/WiFi/Radios/Radio[RADIO5G]"},
            ],
            "AccessPoints": [
                _ap(1, "PRIV0", "WL_PRIV", True, "clave-de-prueba-24",
                    [("3c:22:fb:44:55:66", "192.168.1.40", -58, 72000, "n")]),
                _ap(2, "VID1", "WL_VIDEO_5G", True, "clave-de-prueba-5",
                    [("a4:83:e7:11:22:33", "192.168.1.34", -48, 866000, "ac")]),
                _ap(3, "GUEST2G4", "WL_GUEST2G4", False, "invitados123"),
                _ap(4, "GUEST5G", "WL_GUEST5G", False, "invitados123"),
                _ap(5, "BACKHAUL", "WL_BACKHAUL", False, "interna"),
            ],
            "Radios": [
                {"uid": 1, "Alias": "RADIO2G4", "Enable": True, "Status": "UP", "Channel": 1, "AutoChannelEnable": False,
                 "PossibleChannels": "1,2,3,4,5,6,7,8,9,10,11,12,13", "OperatingChannelBandwidth": "20MHZ",
                 "SupportedChannelBandwidth": "20,40,AUTO", "TransmitPower": -1,
                 "TransmitPowerSupported": "-1,0,10,20,30,40,50,60,70,80,90,100", "OperatingStandards": "g,n",
                 "Stats": {"Noise": -86, "BytesSent": "1000", "BytesReceived": "500", "ErrorsSent": 0, "ErrorsReceived": 0}},
                {"uid": 2, "Alias": "RADIO5G", "Enable": True, "Status": "UP", "Channel": 100, "AutoChannelEnable": False,
                 "PossibleChannels": "36,40,44,48,52,56,60,64,100,104,108,112,116,132,136,140",
                 "OperatingChannelBandwidth": "80MHZ", "SupportedChannelBandwidth": "20,40,AUTO,80",
                 "TransmitPower": -1, "TransmitPowerSupported": "-1,0,10,20,30,40,50,60,70,80,90,100",
                 "OperatingStandards": "n,ac",
                 "Stats": {"Noise": -90, "BytesSent": "9000", "BytesReceived": "800", "ErrorsSent": 1, "ErrorsReceived": 2}},
            ],
            "BandSteering": {"Enable": False, "Status": "DISABLED"},
            "NeighboringWiFiDiagnostic": {"DiagnosticsState": "NONE", "SelectedRadio": "", "Result": []},
        },
    }}


_SEG = re.compile(r'^([^\[]+)(?:\[(.+)\])?$')
_PRED = re.compile(r'^(@?\w+)\s*=\s*["\']?([^"\']*)["\']?$')


class MockClient:
    def __init__(self, *args, **kwargs):
        self._lock = threading.Lock()
        self.tree = _initial_tree()
        self.tree["Device"]["IP"]["Diagnostics"] = {
            "TraceRoute": {"DiagnosticsState": "NONE", "RouteHops": []},
            "SpeedTest": {"DiagnosticsStatus": "NONE", "Download": "", "Upload": "", "Latency": 0,
                          "SelectedServerAddress": "",
                          "History": {"Timestamp": "", "Download": "", "Upload": "", "Latency": "", "SelectedServerAddress": ""}},
        }
        self.password = "1234"
        self.logged_in = True

    # ---- intérprete de xpaths ----

    @staticmethod
    def _split(xpath: str) -> list[str]:
        parts, depth, cur = [], 0, ""
        for ch in xpath:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "/" and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        return [p for p in parts if p]

    def _resolve(self, xpath: str, create_parent=False):
        """Devuelve (contenedor, clave) del último segmento."""
        def fail():
            raise SagemcomError(f"getValue error ({XMO_UNKNOWN_PATH_ERR}): XMO_UNKNOWN_PATH_ERR {xpath}",
                                XMO_UNKNOWN_PATH_ERR)
        node, parent, key = self.tree, None, None
        for seg in self._split(xpath):
            m = _SEG.match(seg)
            if not m:
                fail()
            name, pred = m.group(1), m.group(2)
            if isinstance(node, list):
                # Elemento de una colección: Host[@uid="1"], SSID[Alias="WL_PRIV"]
                if not pred:
                    fail()
                pm = _PRED.match(pred)
                if not pm:
                    fail()
                field, val = pm.group(1).lstrip("@"), pm.group(2)
                idx = next((i for i, el in enumerate(node) if str(el.get(field)) == val), None)
                if idx is None:
                    fail()
                parent, key, node = node, idx, node[idx]
                continue
            if name == "@uid":
                name = "uid"
            if not isinstance(node, dict) or name not in node:
                fail()
            parent, key, node = node, name, node[name]
            if pred:
                pm = _PRED.match(pred)
                if not (pm and isinstance(node, list)):
                    fail()
                field, val = pm.group(1).lstrip("@"), pm.group(2)
                idx = next((i for i, el in enumerate(node) if str(el.get(field)) == val), None)
                if idx is None:
                    fail()
                parent, key, node = node, idx, node[idx]
        return parent, key

    def _tick(self):
        st = self.tree["Device"]["IP"]["Interfaces"][0]["Stats"]
        st["BytesReceived"] += random.randint(200_000, 40_000_000)
        st["BytesSent"] += random.randint(50_000, 4_000_000)
        opt = self.tree["Device"]["Optical"]["Interfaces"][0]
        opt["OpticalSignalLevel"] = random.randint(-19700, -19300)
        opt["Temperature"] = random.randint(37500, 39500)
        self.tree["Device"]["DeviceInfo"]["UpTime"] = int(time.time() - _START) + 86400 * 3

    def _get(self, xpath):
        parent, key = self._resolve(xpath)
        return copy.deepcopy(parent[key])

    # ---- misma interfaz que SagemcomClient ----

    def login(self):
        pass

    def logout(self):
        pass

    def get_value(self, xpath):
        with self._lock:
            self._tick()
            return self._get(xpath)

    def get_values(self, xpaths):
        with self._lock:
            self._tick()
            out = {}
            for name, xp in xpaths.items():
                try:
                    out[name] = self._get(xp)
                except SagemcomError:
                    out[name] = None
            return out

    def set_value(self, xpath, value):
        self.set_values({xpath: value})

    def set_values(self, values):
        with self._lock:
            resolved = [(self._resolve(xp), v) for xp, v in values.items()]  # todo o nada
            for (parent, key), v in resolved:
                parent[key] = v
            self._simulate_scan()

    def _simulate_scan(self):
        nd = self.tree["Device"]["WiFi"]["NeighboringWiFiDiagnostic"]
        if nd["DiagnosticsState"] != "REQUESTED":
            return
        five = "5G" in nd["SelectedRadio"]
        found = ([("Vecino_5G", 36, -60, "80MHz"), ("MOVISTAR_X", 100, -72, "80MHz")] if five else
                 [("Vecino", 1, -55, "20MHz"), ("MOVISTAR_X", 6, -80, "20MHz"), ("", 1, -70, "20MHz")])
        nd["Result"] = [
            {"uid": i + 1, "SSID": s, "BSSID": f"aa:bb:cc:00:00:0{i}", "Channel": ch, "SignalStrength": sig,
             "OperatingChannelBandwidth": bw, "SecurityModeEnabled": "WPA2",
             "OperatingFrequencyBand": "5GHz" if five else "2.4GHz"}
            for i, (s, ch, sig, bw) in enumerate(found)]
        nd["DiagnosticsState"] = "COMPLETED"

    def add_child(self, parent_xpath, value):
        with self._lock:
            parent, key = self._resolve(parent_xpath)
            coll = parent[key]
            if not isinstance(coll, list):
                raise SagemcomError(f"addChild: {parent_xpath} no es una colección")
            (item,) = value.values()
            uid = max((el.get("uid", 0) for el in coll), default=0) + 1
            item = dict(item, uid=uid)
            item.setdefault("Alias", f"cpe-{uid}")
            coll.append(item)
            return {"uid": item["uid"]}

    def delete_child(self, xpath):
        with self._lock:
            parent, key = self._resolve(xpath)
            del parent[key]

    def remote_call(self, xpath, method, parameters=None):
        if method == "ping":
            ok = parameters and parameters.get("Host") != "fallo.invalid"
            count = int((parameters or {}).get("Count", 4))
            return {"PacketsTransmitted": count, "PacketsReceived": count if ok else 0,
                    "AverageResponseTime": 12 if ok else 0}
        if method in ("reboot", "startWPS", "reinitialize", "testPhones", "eject"):
            return {}
        if method == "traceRoute":
            return {"RouteHops": [{"Host": "192.168.144.1", "HostAddress": "192.168.144.1", "RTTimes": "2,2,3"},
                                  {"Host": "one.one.one.one", "HostAddress": "1.1.1.1", "RTTimes": "9,9,10"}]}
        if method == "nslookup":
            return {"Status": "Success", "Answer": "1.1.1.1", "Host": (parameters or {}).get("Host")}
        if method == "speedTestClient":
            st = self.tree["Device"]["IP"]["Diagnostics"]["SpeedTest"]
            st.update({"DiagnosticsStatus": "COMPLETE", "Download": "612.4", "Upload": "598.1", "Latency": 6,
                       "SelectedServerAddress": "speedtest.example.net"})
            h = st["History"]
            for k, v in (("Timestamp", "1791460000"), ("Download", "612.4"), ("Upload", "598.1"), ("Latency", "6"),
                         ("SelectedServerAddress", "speedtest.example.net")):
                h[k] = f"{h[k]},{v}" if h[k] else v
            return {}
        if method == "getVendorLogDownloadURI":
            return {"uri": "/download/log"}
        if method == "getDownloadURI":
            return {"uri": "/download/device.cfg"}
        if method == "changeClearPassword":
            if (parameters or {}).get("OldPassword") != self.password:
                raise SagemcomError("changeClearPassword error (16777223): XMO_AUTHENTICATION_ERR")
            self.password = parameters["NewPassword"]
            return {}
        raise SagemcomError(f"{method} no simulado")

    def reboot(self):
        return self.remote_call("Device", "reboot", {"source": "GUI"})

    def download(self, uri):
        if uri.endswith("log"):
            return b"08.10.2026 15:00:01 info kernel: arranque\n08.10.2026 15:00:05 warn wifi: canal ocupado\n"
        return b"<config>simulada</config>"

    def set_password(self, password):
        pass

    def request(self, actions, priority=False):
        raise SagemcomError("raw request no disponible en modo simulado")
