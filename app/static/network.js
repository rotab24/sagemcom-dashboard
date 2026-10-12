"use strict";
// Pestaña "Red y seguridad": abrir puertos, IPs fijas, DMZ, DHCP/DNS, firewall y UPnP.

let netData = null;

function hostLabel(ip) {
  const h = hostsCache.find((x) => x.ip === ip);
  return h ? `${esc(h.name)} <span class="muted small mono">${esc(ip)}</span>` : `<span class="mono">${esc(ip)}</span>`;
}

function fillHostSelects() {
  // Dispositivos con IP de la red local, primero los conectados
  const hosts = hostsCache.filter((h) => h.ip).sort((a, b) => (b.active - a.active) || a.name.localeCompare(b.name));
  document.querySelectorAll(".host-select").forEach((sel) => {
    const cur = sel.value;
    sel.innerHTML = '<option value="">— Elige dispositivo —</option>' +
      hosts.map((h) => `<option value="${esc(h.ip)}">${esc(h.name)} (${esc(h.ip)})${h.active ? "" : " · desconectado"}</option>`).join("");
    sel.value = cur;
  });
  document.querySelectorAll(".host-select-mac").forEach((sel) => {
    sel.innerHTML = '<option value="">— Elige dispositivo —</option>' +
      hosts.map((h) => `<option value="${esc(h.mac)}" data-ip="${esc(h.ip)}">${esc(h.name)} (${esc(h.ip)})</option>`).join("");
  });
}

function pfPorts(r) {
  const ext = r.external_port_end && r.external_port_end !== r.external_port ? `${r.external_port}–${r.external_port_end}` : `${r.external_port}`;
  return r.internal_port && r.internal_port !== r.external_port ? `${ext} → ${r.internal_port}` : ext;
}

const PROTO = { TCP: "TCP", UDP: "UDP", BOTH: "TCP y UDP" };

async function loadNetwork() {
  if (!hostsCache.length) await loadHosts();
  netData = await api("/api/network");
  fillHostSelects();
  const n = netData;

  $("#pf-table tbody").innerHTML = n.port_forwards.map((r) => `<tr class="${r.enabled ? "" : "inactive"}">
      <td>${esc(r.description || r.alias)}</td>
      <td class="mono">${pfPorts(r)}</td>
      <td>${PROTO[r.protocol] || esc(r.protocol)}</td>
      <td>${hostLabel(r.internal_client)}</td>
      <td><div class="actions">
        <button class="small" data-pf-toggle="${r.uid}">${r.enabled ? "Desactivar" : "Activar"}</button>
        <button class="small danger" data-pf-del="${r.uid}">Cerrar</button>
      </div></td></tr>`).join("") || '<tr><td colspan="5" class="muted">No hay puertos abiertos</td></tr>';

  $("#upnp-rules").innerHTML = n.upnp_rules.map((r) => `<li><span>${esc(r.description || "Sin nombre")} · <span class="mono">${pfPorts(r)}</span> ${PROTO[r.protocol] || esc(r.protocol)}</span><span>${hostLabel(r.internal_client)}</span></li>`).join("") ||
    `<li class="muted">Ninguno${n.upnp.enabled ? "" : " (UPnP desactivado)"}</li>`;

  $("#reservations").innerHTML = n.reservations.map((r) => {
    const h = hostsCache.find((x) => x.mac === r.mac);
    return `<li><span>${esc(h ? h.name : r.mac)} → <b class="mono">${esc(r.ip)}</b></span>
      <button class="small danger" data-res-del="${esc(r.alias)}">Quitar</button></li>`;
  }).join("") || '<li class="muted">Ningún dispositivo tiene IP fija</li>';

  const dmz = $("#dmz-form");
  dmz.enabled.checked = n.dmz.enabled;
  dmz.ip.value = n.dmz.ip;

  $("#lan-info").textContent = `Router: ${n.lan.ip} · red ${n.lan.network} · ${n.dhcp.clients} aparatos con IP automática`;
  const dh = $("#dhcp-form");
  dh.start.value = n.dhcp.start;
  dh.end.value = n.dhcp.end;
  if (![...dh.lease_time.options].some((o) => Number(o.value) === n.dhcp.lease_time)) {
    dh.lease_time.add(new Option(`${Math.round(n.dhcp.lease_time / 3600)} h (actual)`, n.dhcp.lease_time));
  }
  dh.lease_time.value = String(n.dhcp.lease_time);

  const dns = $("#dns-form");
  dns.dns1.value = n.dhcp.dns_servers[0] || "";
  dns.dns2.value = n.dhcp.dns_servers[1] || "";

  const fw = $("#fw-form");
  fw.level.value = n.firewall.level;
  fw.respond_to_ping.checked = n.firewall.respond_to_ping;
  fw.port_scan_detection.checked = n.firewall.port_scan_detection;
  fw.block_fragments.checked = n.firewall.block_fragments;
  fw.upnp.checked = n.upnp.enabled;
}

const intOrNull = (v) => (v === "" || v == null ? null : Number(v));

$("#pf-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    description: f.description.value, protocol: f.protocol.value, internal_client: f.internal_client.value,
    external_port: Number(f.external_port.value), external_port_end: intOrNull(f.external_port_end.value),
    internal_port: intOrNull(f.internal_port.value),
  };
  if (await act(() => api("/api/network/portforwards", { method: "POST", body }), `Puerto ${body.external_port} abierto`)) {
    f.reset();
    f.closest("details").open = false;
    loadNetwork();
  }
});

$("#pf-table").addEventListener("click", async (e) => {
  const del = e.target.closest("[data-pf-del]");
  const tog = e.target.closest("[data-pf-toggle]");
  if (del) {
    const r = netData.port_forwards.find((x) => x.uid === Number(del.dataset.pfDel));
    if (!confirm(`¿Cerrar el puerto ${pfPorts(r)} (${r.description || r.alias})?`)) return;
    if (await act(() => api(`/api/network/portforwards/${r.uid}`, { method: "DELETE" }), "Puerto cerrado")) loadNetwork();
  } else if (tog) {
    const r = netData.port_forwards.find((x) => x.uid === Number(tog.dataset.pfToggle));
    const body = { description: r.description, protocol: r.protocol, external_port: r.external_port,
      external_port_end: r.external_port_end || null, internal_client: r.internal_client,
      internal_port: r.internal_port, enabled: !r.enabled };
    if (await act(() => api(`/api/network/portforwards/${r.uid}`, { method: "PUT", body }), r.enabled ? "Regla desactivada" : "Regla activada")) loadNetwork();
  }
});

$("#res-form").mac.addEventListener("change", (e) => {
  const opt = e.target.selectedOptions[0];
  if (opt && opt.dataset.ip && !$("#res-form").ip.value) $("#res-form").ip.value = opt.dataset.ip;
});

$("#res-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  if (await act(() => api("/api/network/reservations", { method: "POST", body: { mac: f.mac.value, ip: f.ip.value.trim() } }), "IP fija reservada")) {
    f.reset();
    loadNetwork();
  }
});

$("#reservations").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-res-del]");
  if (!b || !confirm("¿Quitar esta IP fija? El aparato volverá a recibir una IP automática.")) return;
  if (await act(() => api(`/api/network/reservations/${encodeURIComponent(b.dataset.resDel)}`, { method: "DELETE" }), "Reserva eliminada")) loadNetwork();
});

$("#dmz-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const enabled = f.enabled.checked;
  if (enabled && !confirm("Con la DMZ, ese aparato queda totalmente expuesto a Internet. ¿Seguro?")) return;
  if (await act(() => api("/api/network/dmz", { method: "PUT", body: { enabled, ip: f.ip.value } }), enabled ? "DMZ activada" : "DMZ desactivada")) loadNetwork();
});

$("#dhcp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { start: f.start.value.trim(), end: f.end.value.trim(), lease_time: Number(f.lease_time.value) };
  if (!confirm("Los aparatos cogerán la nueva configuración cuando renueven su IP. ¿Guardar?")) return;
  if (await act(() => api("/api/network/dhcp", { method: "PUT", body }), "DHCP guardado")) loadNetwork();
});

$("#dns-form").addEventListener("click", (e) => {
  const b = e.target.closest("[data-dns]");
  if (!b) return;
  const list = b.dataset.dns === "op" ? netData.dhcp.wan_dns : b.dataset.dns.split(",");
  $("#dns-form").dns1.value = list[0] || "";
  $("#dns-form").dns2.value = list[1] || "";
});

$("#dns-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const servers = [f.dns1.value, f.dns2.value].filter((s) => s.trim());
  if (await act(() => api("/api/network/dns", { method: "PUT", body: { servers } }), "DNS guardados (los aparatos los usarán al renovar su IP)")) loadNetwork();
});

$("#fw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { level: f.level.value, respond_to_ping: f.respond_to_ping.checked,
    port_scan_detection: f.port_scan_detection.checked, block_fragments: f.block_fragments.checked };
  if (body.level !== netData.firewall.level && body.level === "HIGH" &&
      !confirm("El nivel Alto bloquea muchas conexiones salientes y puede romper juegos, videollamadas o apps. ¿Continuar?")) return;
  const ok = await act(async () => {
    await api("/api/network/firewall", { method: "PUT", body });
    if (f.upnp.checked !== netData.upnp.enabled) await api("/api/network/upnp", { method: "PUT", body: { enabled: f.upnp.checked } });
  }, "Guardado");
  if (ok) loadNetwork();
});

loaders.red = () => loadNetwork().catch((e) => showError(e.message));
