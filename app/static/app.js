"use strict";

const REFRESH_MS = 15000;
const DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"];
const DAY_NAMES = { MONDAY: "Lunes", TUESDAY: "Martes", WEDNESDAY: "Miércoles", THURSDAY: "Jueves", FRIDAY: "Viernes", SATURDAY: "Sábado", SUNDAY: "Domingo" };
const $ = (sel) => document.querySelector(sel);

// ---- utilidades -------------------------------------------------------------

// Valores que el servidor ha releído del router tras escribirlos (cabecera X-Router-Verified).
// El siguiente aviso de "hecho" lo indica: así se sabe que el cambio está aplicado, no solo enviado.
let verifiedPending = { n: 0, at: 0 };

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const verified = Number(res.headers.get("X-Router-Verified") || 0);
  if (verified) verifiedPending = { n: verifiedPending.n + verified, at: Date.now() };
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map((e) => e.msg).join(", ") : d || `HTTP ${res.status}`);
  }
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtBps(bps) {
  if (bps == null) return "—";
  const units = ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"];
  let i = 0;
  while (bps >= 1000 && i < units.length - 1) { bps /= 1000; i++; }
  return `${bps.toFixed(bps < 10 ? 1 : 0)} ${units[i]}`;
}

function fmtUptime(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : `${h} h ${m} min`;
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function hhmm(ts) {
  return new Date(ts * 1000).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}

function showError(msg) {
  const el = $("#error");
  el.hidden = !msg;
  el.textContent = msg || "";
}

let toastTimer;
function toast(msg) {
  const el = $("#toast");
  if (verifiedPending.n && Date.now() - verifiedPending.at < 5000) {
    msg += ` · comprobado en el router (${verifiedPending.n} ${verifiedPending.n === 1 ? "valor" : "valores"})`;
  }
  verifiedPending = { n: 0, at: 0 };
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

async function act(fn, okMsg) {
  try {
    await fn();
    if (okMsg) toast(okMsg);
    return true;
  } catch (err) {
    alert(err.message);
    return false;
  }
}

// ---- pestañas ------------------------------------------------------------------

const loaders = {};
let currentTab = "resumen";

function showTab(name) {
  if (!document.getElementById(`tab-${name}`)) name = "resumen";
  currentTab = name;
  document.querySelectorAll(".tab").forEach((t) => { t.hidden = t.id !== `tab-${name}`; });
  document.querySelectorAll("#tabs a").forEach((a) => {
    a.classList.toggle("active", a.dataset.tab === name);
    if (a.dataset.tab === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  (loaders[name] || (() => {}))();
}

window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
// El enlace de salto no cambia de pestaña (el # sirve para eso): solo lleva el foco al contenido
document.querySelector("[data-skip]").addEventListener("click", (e) => {
  e.preventDefault();
  document.getElementById("contenido").focus();
});
document.querySelectorAll("[data-goto]").forEach((el) => el.addEventListener("click", () => { location.hash = el.dataset.goto; }));

// ---- resumen --------------------------------------------------------------------

async function loadStatus() {
  const s = await api("/api/status");
  const d = s.device, w = s.wan, g = s.gpon;
  $("#model").textContent = d.model || "Sagemcom";
  $("#subtitle").textContent = `${s.host} · firmware ${d.software || "?"}`;
  $("#mock-badge").hidden = !s.mock;
  $("#wan-status").innerHTML = w.up ? '<span class="ok">Conectado</span>' : `<span class="bad">${esc(w.status || "Caído")}</span>`;
  $("#wan-ip").textContent = w.ipv4 ? `IP pública ${w.ipv4}` : "";
  let fibra = "—";
  if (g.rx_dbm != null) {
    const ok = g.rx_dbm > -27 && g.rx_dbm < -8;
    fibra = `<span class="${ok ? "ok" : "bad"}">${g.rx_dbm} dBm</span>`;
  }
  $("#gpon-rx").innerHTML = fibra;
  $("#gpon-extra").textContent = [g.rx_dbm != null ? (g.rx_dbm > -27 && g.rx_dbm < -8 ? "Señal correcta" : "Señal fuera de rango") : null,
    g.temperature != null ? `${g.temperature} °C` : null].filter(Boolean).join(" · ");
  $("#uptime").textContent = fmtUptime(d.uptime);
  $("#mem").textContent = d.mem_used_pct != null ? `Encendido · memoria ${d.mem_used_pct} %` : "Encendido";
  $("#updated").textContent = `Actualizado ${new Date().toLocaleTimeString("es-ES")}`;
}

async function loadHistory() {
  const hours = Number($("#range").value);
  const rows = await api(`/api/history?hours=${hours}`);
  const last = [...rows].reverse().find((r) => r.rate_rx_bps != null);
  $("#rate-rx").textContent = last ? `↓ ${fmtBps(last.rate_rx_bps)}` : "—";
  $("#rate-tx").textContent = last ? `↑ ${fmtBps(last.rate_tx_bps)}` : "Esperando 2 muestras…";
  drawChart(rows.filter((r) => r.rate_rx_bps != null), hours);
}

function drawChart(rows, hours) {
  const el = $("#chart");
  if (rows.length < 2) {
    el.innerHTML = '<p class="muted">Aún no hay suficientes muestras. Se guarda una cada minuto.</p>';
    return;
  }
  const W = el.clientWidth || 800, H = el.clientHeight || 200, padL = 70, padB = 20, padT = 8;
  const t1 = Date.now() / 1000, t0 = t1 - hours * 3600;
  const max = Math.max(...rows.map((r) => Math.max(r.rate_rx_bps, r.rate_tx_bps)), 1);
  const x = (t) => padL + ((t - t0) / (t1 - t0)) * (W - padL - 4);
  const y = (v) => padT + (1 - v / max) * (H - padT - padB);
  const path = (key) => rows.map((r, i) => `${i ? "L" : "M"}${x(r.ts).toFixed(1)},${y(r[key]).toFixed(1)}`).join("");
  let grid = "";
  for (let i = 0; i <= 3; i++) {
    const v = (max * i) / 3, yy = y(v);
    grid += `<line class="grid" x1="${padL}" x2="${W}" y1="${yy}" y2="${yy}"/><text x="${padL - 6}" y="${yy + 4}" text-anchor="end">${fmtBps(v)}</text>`;
  }
  for (let i = 0; i <= 4; i++) {
    const t = t0 + ((t1 - t0) * i) / 4;
    const label = new Date(t * 1000).toLocaleString("es-ES", hours > 24 ? { day: "2-digit", month: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
    const anchor = i === 0 ? "start" : i === 4 ? "end" : "middle";
    grid += `<text x="${x(t)}" y="${H - 4}" text-anchor="${anchor}">${label}</text>`;
  }
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}
    <path d="${path("rate_rx_bps")}" fill="none" stroke="var(--rx)" stroke-width="1.6"/>
    <path d="${path("rate_tx_bps")}" fill="none" stroke="var(--tx)" stroke-width="1.6"/></svg>`;
}

async function loadEvents() {
  const ev = await api("/api/events?limit=60");
  $("#events").innerHTML = ev.map((e) => `<li class="${esc(e.kind)}"><time>${fmtTime(e.ts)}</time>${esc(e.message)}</li>`).join("") ||
    '<li class="muted">Sin eventos todavía</li>';
}

$("#range").addEventListener("change", () => loadHistory().catch((e) => showError(e.message)));
window.addEventListener("resize", () => { if (currentTab === "resumen") loadHistory().catch(() => {}); });

// ---- dispositivos ----------------------------------------------------------------

let hostsCache = [];
let editingUid = null;

async function loadHosts() {
  hostsCache = await api("/api/hosts");
  renderHostsSummary();
  if (currentTab === "dispositivos") { fillGroupFilter(); renderHosts(); }
}

function renderHostsSummary() {
  const active = hostsCache.filter((h) => h.active);
  const unknown = active.filter((h) => !h.known);
  const blocked = hostsCache.filter((h) => h.block === "always").length;
  $("#hosts-count").textContent = `${active.length} conectados`;
  // Los aparatos sin revisar se resaltan en la propia tarjeta: un aviso aparte
  // aparecía después de cargar y desplazaba todo lo de debajo (CLS)
  const extra = $("#hosts-extra");
  extra.textContent = [unknown.length ? `${unknown.length} sin revisar` : "Todos revisados", blocked ? `${blocked} bloqueados` : null].filter(Boolean).join(" · ");
  extra.classList.toggle("attention", unknown.length > 0);
}

function blockPill(h) {
  if (h.blocked_until) return `<span class="pill blocked">Bloqueado hasta las ${hhmm(h.blocked_until)}</span>`;
  if (h.block === "always") return '<span class="pill blocked">Bloqueado</span>';
  if (h.block === "schedule") return '<span class="pill schedule">Con horario</span>';
  return '<span class="pill">Libre</span>';
}

// Estado de la tabla (orden y agrupación se recuerdan en este navegador)
const hostView = { sort: "ip", dir: 1, filters: {}, selected: new Set() };
const COLS = 9;

function renderHosts() {
  const q = $("#host-search").value.trim();
  const groupBy = $("#host-groupby").value;
  const f = hostView.filters;
  const filtering = q || Object.values(f).some(Boolean);
  // Al buscar o filtrar se incluyen también los desconectados
  const showInactive = $("#show-inactive").checked || filtering;
  const terms = norm(q).split(/\s+/).filter(Boolean);
  const rows = hostsCache.filter((h) => (showInactive || h.active || h.block !== "none") &&
    terms.every((t) => hostHaystack(h).includes(t)) && matchesColumnFilters(h, f));
  rows.sort(hostComparator());
  hostView.visible = rows;
  $("#hosts-shown").textContent = `${rows.length} de ${hostsCache.length}`;
  let html;
  if (!groupBy) {
    html = rows.map(hostRow).join("");
  } else {
    const sections = new Map();
    for (const h of rows) {
      for (const key of hostGroupKeys(h, groupBy)) {
        if (!sections.has(key)) sections.set(key, []);
        sections.get(key).push(h);
      }
    }
    // Orden alfabético, con "Sin …" al final
    const keys = [...sections.keys()].sort((a, b) => (a.startsWith("Sin ") - b.startsWith("Sin ")) || a.localeCompare(b, "es"));
    html = keys.map((k) => `<tr class="group-row"><th colspan="${COLS}">${esc(k)} <span class="muted">(${sections.get(k).length})</span></th></tr>` +
      sections.get(k).map(hostRow).join("")).join("");
  }
  $("#hosts tbody").innerHTML = html || `<tr><td colspan="${COLS}" class="muted">Nada que mostrar</td></tr>`;
  document.querySelectorAll("#hosts th[data-sort]").forEach((th) => {
    const on = th.dataset.sort === hostView.sort;
    th.classList.toggle("sorted", on);
    th.dataset.dir = on ? (hostView.dir > 0 ? "asc" : "desc") : "";
    th.setAttribute("aria-sort", on ? (hostView.dir > 0 ? "ascending" : "descending") : "none");
  });
  renderBulkBar();
}

// Búsqueda sin distinguir mayúsculas ni tildes
const norm = (s) => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function internetLabel(h) {
  if (h.blocked_until || h.block === "always") return "Bloqueado";
  return h.block === "schedule" ? "Con horario" : "Libre";
}

function hostHaystack(h) {
  return norm([
    h.name, h.ip, h.fixed_ip, h.fixed_ip ? "fija" : "", h.mac, h.mac.replace(/:/g, ""), h.type, h.detected_type,
    h.interface, h.source, h.groups.join(" "), h.notes, internetLabel(h), h.active ? "conectado" : "desconectado",
    h.known ? "" : "nuevo",
  ].join(" "));
}

const ipNum = (ip) => {
  const p = String(ip || "").split(".").map(Number);
  return p.length === 4 && p.every((n) => n >= 0 && n <= 255) ? ((p[0] * 256 + p[1]) * 256 + p[2]) * 256 + p[3] : null;
};

function sortValue(h, key) {
  switch (key) {
    case "ip": return ipNum(h.ip || h.fixed_ip);
    case "groups": return h.groups.join(", ");
    case "internet": return internetLabel(h);
    default: return h[key] || "";
  }
}

function hostComparator() {
  const { sort, dir } = hostView;
  return (a, b) => {
    const va = sortValue(a, sort), vb = sortValue(b, sort);
    // Vacíos siempre al final, en los dos sentidos
    const ea = va === null || va === "", eb = vb === null || vb === "";
    if (ea || eb) return ea - eb || (ipNum(a.ip) ?? Infinity) - (ipNum(b.ip) ?? Infinity);
    const c = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb), "es", { numeric: true, sensitivity: "base" });
    return c * dir || (ipNum(a.ip) ?? Infinity) - (ipNum(b.ip) ?? Infinity);
  };
}

function matchesColumnFilters(h, f) {
  if (f.name && !norm(h.name).includes(norm(f.name))) return false;
  if (f.mac && !norm(h.mac).replace(/[:-]/g, "").includes(norm(f.mac).replace(/[:-]/g, ""))) return false;
  if (f.ip && ![h.ip, h.fixed_ip].some((ip) => (ip || "").includes(f.ip.trim()))) return false;
  if (f.type && (f.type === "__none" ? h.type : h.type !== f.type)) return false;
  if (f.groups && (f.groups === "__none" ? h.groups.length : !h.groups.includes(f.groups))) return false;
  if (f.interface && (h.interface || "") !== f.interface) return false;
  if (f.internet && internetLabel(h) !== f.internet) return false;
  return true;
}

function hostGroupKeys(h, by) {
  if (by === "type") return [h.type || "Sin tipo"];
  if (by === "group") return h.groups.length ? h.groups : ["Sin grupo"];
  if (by === "interface") return [h.interface || "Sin conexión conocida"];
  return [h.block === "always" ? "Bloqueados" : h.active ? "Conectados" : "Desconectados"];
}

function hostIpCell(h) {
  if (!h.fixed_ip) return h.ip ? esc(h.ip) : '<span class="muted">—</span>';
  if (!h.ip || h.ip === h.fixed_ip) return `${esc(h.fixed_ip)} <span class="badge fixed" title="IP fija">fija</span>`;
  return `${esc(h.ip)}<div class="sub">fija: ${esc(h.fixed_ip)} (al reconectar)</div>`;
}

function hostRow(h) {
  const note = h.notes ? `<div class="sub note" title="${esc(h.notes)}">${esc(h.notes.length > 70 ? h.notes.slice(0, 70) + "…" : h.notes)}</div>` : "";
  const groups = h.groups.length ? h.groups.map((g) => `<span class="tag">${esc(g)}</span>`).join(" ") : '<span class="muted">—</span>';
  const type = h.type
    ? `${esc(h.type)}${h.user_type ? "" : '<div class="sub">detectado</div>'}`
    : '<span class="muted">—</span>';
  const sel = hostView.selected.has(h.mac);
  return `<tr class="${h.active ? "" : "inactive"}${sel ? " selected" : ""}">
    <td class="c-sel"><input type="checkbox" data-select="${esc(h.mac)}" ${sel ? "checked" : ""} aria-label="Seleccionar ${esc(h.name)}"></td>
    <td class="c-name"><span class="dot ${h.active ? "on" : ""}"></span><span class="name">${esc(h.name)}</span>
      <button class="icon-btn" title="Editar ficha" aria-label="Editar ficha de ${esc(h.name)}" data-act="edit" data-uid="${h.uid}"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-edit"/></svg></button>
      ${h.known ? "" : '<span class="badge new">nuevo</span>'}${note}</td>
    <td class="c-mac mono">${esc(h.mac)}</td>
    <td class="c-ip mono editable" title="Cambiar o dejar fija la IP" data-act="edit" data-focus="fixed_ip" data-uid="${h.uid}">${hostIpCell(h)}</td>
    <td class="c-type editable" title="Cambiar tipo" data-act="edit" data-focus="type" data-uid="${h.uid}">${type}</td>
    <td class="c-groups editable" title="Cambiar grupos" data-act="edit" data-focus="groups" data-uid="${h.uid}">${groups}</td>
    <td class="c-conn">${esc(h.interface)}</td>
    <td class="c-net">${blockPill(h)}</td>
    <td class="c-menu"><button class="icon-btn kebab" title="Acciones" aria-label="Acciones de ${esc(h.name)}" aria-haspopup="menu" aria-expanded="false" data-act="menu" data-uid="${h.uid}"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-more"/></svg></button></td>
  </tr>`;
}

// Opciones de los desplegables que dependen de los datos (filtros, acciones en bloque)
function fillGroupFilter() {
  const opts = (values, current, all, none) => `<option value="">${all}</option>` +
    (none ? `<option value="__none"${current === "__none" ? " selected" : ""}>${none}</option>` : "") +
    values.map((v) => `<option${v === current ? " selected" : ""}>${esc(v)}</option>`).join("");
  const uniq = (arr) => [...new Set(arr.filter(Boolean))].sort((a, b) => a.localeCompare(b, "es"));
  const f = hostView.filters;
  const set = (key, html) => { document.querySelector(`#host-filters [data-filter="${key}"]`).innerHTML = html; };
  set("type", opts(uniq(hostsCache.map((h) => h.type)), f.type, "Todos", "Sin tipo"));
  set("groups", opts(allGroupNames(), f.groups, "Todos", "Sin grupo"));
  set("interface", opts(uniq(hostsCache.map((h) => h.interface)), f.interface, "Todas"));
  set("internet", opts(["Libre", "Con horario", "Bloqueado"], f.internet, "Todos"));
  renderBulkBar();
}

function allGroupNames() {
  const fromGroups = typeof groupsData !== "undefined" ? groupsData.map((g) => g.name) : [];
  return [...new Set([...fromGroups, ...hostsCache.flatMap((h) => h.groups)])].sort((a, b) => a.localeCompare(b, "es"));
}

async function loadParentalNote() {
  const p = await api("/api/parental");
  const note = $("#parental-note");
  note.hidden = p.enabled;
  note.textContent = "El control parental del router está desactivado. Se activará automáticamente la primera vez que bloquees un dispositivo o guardes un horario (solo afecta a los dispositivos que configures).";
}

// ---- ordenar y filtrar por columnas ------------------------------------------------------

$("#hosts thead").addEventListener("click", (e) => {
  const th = e.target.closest("th[data-sort]");
  if (!th) return;
  if (hostView.sort === th.dataset.sort) hostView.dir = -hostView.dir;
  else { hostView.sort = th.dataset.sort; hostView.dir = 1; }
  saveHostPrefs();
  renderHosts();
});

$("#host-filters").addEventListener("input", (e) => {
  const el = e.target.closest("[data-filter]");
  if (!el) return;
  hostView.filters[el.dataset.filter] = el.value;
  renderHosts();
});

$("#host-filters-clear").addEventListener("click", () => {
  hostView.filters = {};
  document.querySelectorAll("#host-filters [data-filter]").forEach((el) => { el.value = ""; });
  renderHosts();
});

function setFiltersVisible(on) {
  $("#host-filters").hidden = !on;
  $("#host-filters-toggle").setAttribute("aria-pressed", String(on));
  $("#host-filters-toggle").classList.toggle("on", on);
}

$("#host-filters-toggle").addEventListener("click", () => {
  const on = $("#host-filters").hidden;
  setFiltersVisible(on);
  if (!on) $("#host-filters-clear").click(); // al ocultarlos, que no sigan filtrando a escondidas
  saveHostPrefs();
});

// ---- selección múltiple y acciones en bloque ------------------------------------------------

$("#hosts").addEventListener("change", (e) => {
  const cb = e.target.closest("input[data-select]");
  if (cb) {
    cb.checked ? hostView.selected.add(cb.dataset.select) : hostView.selected.delete(cb.dataset.select);
    cb.closest("tr").classList.toggle("selected", cb.checked);
    renderBulkBar();
  } else if (e.target.id === "host-select-all") {
    const macs = (hostView.visible || []).map((h) => h.mac);
    macs.forEach((m) => (e.target.checked ? hostView.selected.add(m) : hostView.selected.delete(m)));
    renderHosts();
  }
});

function selectedHosts() {
  return hostsCache.filter((h) => hostView.selected.has(h.mac));
}

function renderBulkBar() {
  // Quitar de la selección lo que ya no existe
  const macs = new Set(hostsCache.map((h) => h.mac));
  for (const m of hostView.selected) if (!macs.has(m)) hostView.selected.delete(m);
  const sel = selectedHosts();
  const visible = hostView.visible || [];
  const all = $("#host-select-all");
  const nVisibleSel = visible.filter((h) => hostView.selected.has(h.mac)).length;
  all.checked = visible.length > 0 && nVisibleSel === visible.length;
  all.indeterminate = nVisibleSel > 0 && nVisibleSel < visible.length;
  $("#hosts-bulk").hidden = !sel.length;
  if (!sel.length) return;
  $("#bulk-count").textContent = `${sel.length} seleccionado${sel.length === 1 ? "" : "s"}`;
  const groups = allGroupNames();
  $("#bulk-add-group").innerHTML = '<option value="">Añadir a grupo…</option>' +
    groups.map((g) => `<option>${esc(g)}</option>`).join("") + '<option value="__new">+ Nuevo grupo…</option>';
  const inSel = [...new Set(sel.flatMap((h) => h.groups))].sort((a, b) => a.localeCompare(b, "es"));
  $("#bulk-remove-group").innerHTML = '<option value="">Quitar de grupo…</option>' + inSel.map((g) => `<option>${esc(g)}</option>`).join("");
  $("#bulk-remove-group").disabled = !inSel.length;
  const types = [...new Set([...TYPE_PRESETS, ...hostsCache.map((h) => h.user_type).filter(Boolean)])];
  $("#bulk-type").innerHTML = '<option value="">Poner tipo…</option>' + types.map((t) => `<option>${esc(t)}</option>`).join("") +
    '<option value="__other">Otro…</option><option value="__clear">Quitar (usar el detectado)</option>';
}

async function bulkDb(action, value = "") {
  const macs = [...hostView.selected];
  let res;
  if (await act(async () => { res = await api("/api/hosts/bulk", { method: "POST", body: { macs, action, value } }); })) {
    toast(res.message);
    await loadHosts();
    if (action.endsWith("group") && typeof loadGroups === "function") loadGroups().catch(() => {});
  }
}

// Bloqueos de uno en uno: cada uno son varias escrituras en el router, que es lento
async function bulkBlock(blocked, minutes = null) {
  const list = selectedHosts().filter((h) => (blocked ? h.block !== "always" : h.block !== "none" || h.blocked_until));
  if (!list.length) { toast(blocked ? "Ya estaban todos bloqueados" : "Ninguno estaba bloqueado"); return; }
  const what = blocked ? (minutes ? `durante ${minutes} minutos` : "hasta que los desbloquees") : "";
  if (!confirm(blocked ? `¿Quitar Internet a ${list.length} dispositivo(s) ${what}?` : `¿Desbloquear ${list.length} dispositivo(s)? (también se borra su horario)`)) return;
  const errors = [];
  for (const [i, h] of list.entries()) {
    toast(`${blocked ? "Bloqueando" : "Desbloqueando"} ${i + 1}/${list.length}: ${h.name}`);
    try {
      await api(`/api/hosts/${h.uid}/block`, { method: "POST", body: { blocked, minutes } });
    } catch (err) {
      errors.push(`${h.name}: ${err.message}`);
    }
  }
  if (errors.length) alert(`No se pudo con ${errors.length}:\n` + errors.join("\n"));
  else toast(`${list.length} dispositivo(s) ${blocked ? "bloqueados" : "desbloqueados"}`);
  loadHosts();
  loadParentalNote().catch(() => {});
}

$("#hosts-bulk").addEventListener("change", async (e) => {
  const el = e.target;
  const v = el.value;
  if (!v) return;
  el.value = "";
  if (el.id === "bulk-add-group") {
    const name = v === "__new" ? (prompt("Nombre del nuevo grupo (p. ej. IoT):") || "").trim() : v;
    if (name) await bulkDb("add_group", name);
  } else if (el.id === "bulk-remove-group") {
    await bulkDb("remove_group", v);
  } else if (el.id === "bulk-type") {
    const type = v === "__clear" ? "" : v === "__other" ? (prompt("Tipo:") || "").trim() : v;
    if (v === "__clear" || type) await bulkDb("set_type", type);
  } else if (el.id === "bulk-block") {
    await bulkBlock(true, v === "always" ? null : Number(v));
  }
});

$("#hosts-bulk").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-bulk]");
  if (!b) return;
  switch (b.dataset.bulk) {
    case "unblock": await bulkBlock(false); break;
    case "known": await bulkDb("known"); break;
    case "export": downloadCsv([...hostView.selected]); break;
    case "clear": hostView.selected.clear(); renderHosts(); break;
  }
});

// ---- menú ⋯ de cada fila ---------------------------------------------------------------------

const rowMenu = $("#row-menu");

function openRowMenu(btn, h) {
  const blocked = h.block === "always" || h.blocked_until;
  const items = [
    ["edit", "Editar ficha…"],
    ...(blocked
      ? [["unblock", "Desbloquear"]]
      : [["block:30", "Bloquear 30 minutos"], ["block:60", "Bloquear 1 hora"], ["block:120", "Bloquear 2 horas"], ["block:always", "Bloquear hasta que lo desbloquee"]]),
    ["schedule", "Horario sin Internet…"],
    ["known", h.known ? "Olvidar (marcar como no revisado)" : "Lo conozco"],
    ["-"],
    ["copy:mac", "Copiar MAC"],
    ...(h.ip ? [["copy:ip", "Copiar IP"]] : []),
  ];
  rowMenu.innerHTML = items.map(([act, label]) => act === "-" ? '<hr>' :
    `<button type="button" role="menuitem" data-menu="${act}" class="${act.startsWith("block") ? "danger-text" : ""}">${esc(label)}</button>`).join("");
  rowMenu.dataset.uid = h.uid;
  rowMenu.hidden = false;
  document.querySelectorAll("[data-act=menu][aria-expanded=true]").forEach((b) => b.setAttribute("aria-expanded", "false"));
  btn.setAttribute("aria-expanded", "true");
  const r = btn.getBoundingClientRect();
  const m = rowMenu.getBoundingClientRect();
  const top = r.bottom + m.height + 8 > innerHeight ? r.top - m.height - 4 : r.bottom + 4;
  rowMenu.style.top = `${Math.max(8, top)}px`;
  rowMenu.style.left = `${Math.max(8, Math.min(r.right - m.width, innerWidth - m.width - 8))}px`;
  rowMenu.querySelector("button").focus();
}

function closeRowMenu() {
  rowMenu.hidden = true;
  document.querySelectorAll("[data-act=menu][aria-expanded=true]").forEach((b) => b.setAttribute("aria-expanded", "false"));
}

document.addEventListener("click", (e) => { if (!rowMenu.hidden && !e.target.closest("#row-menu, [data-act=menu]")) closeRowMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !rowMenu.hidden) closeRowMenu(); });
window.addEventListener("scroll", closeRowMenu, true);
window.addEventListener("resize", closeRowMenu);

rowMenu.addEventListener("keydown", (e) => {
  if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
  e.preventDefault();
  const items = [...rowMenu.querySelectorAll("button")];
  const i = items.indexOf(document.activeElement);
  items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
});

rowMenu.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-menu]");
  if (!b) return;
  const h = hostsCache.find((x) => x.uid === Number(rowMenu.dataset.uid));
  closeRowMenu();
  if (!h) return;
  const [act_, arg] = b.dataset.menu.split(":");
  switch (act_) {
    case "edit": openDevice(h); break;
    case "schedule": openSchedule(h); break;
    case "known":
      await api(`/api/hosts/${encodeURIComponent(h.mac)}/known`, { method: "POST", body: { known: !h.known } });
      loadHosts();
      break;
    case "unblock":
      if (await act(() => api(`/api/hosts/${h.uid}/block`, { method: "POST", body: { blocked: false } }), `${h.name} desbloqueado`)) loadHosts();
      break;
    case "block": {
      const minutes = arg === "always" ? null : Number(arg);
      if (!confirm(minutes ? `¿Quitar Internet a ${h.name} durante ${minutes} minutos?` : `¿Quitar Internet a ${h.name} hasta que lo desbloquees?`)) return;
      if (await act(() => api(`/api/hosts/${h.uid}/block`, { method: "POST", body: { blocked: true, minutes } }), `${h.name} bloqueado`)) {
        loadHosts();
        loadParentalNote().catch(() => {});
      }
      break;
    }
    case "copy": {
      const text = arg === "mac" ? h.mac : h.ip;
      try { await navigator.clipboard.writeText(text); toast(`Copiado: ${text}`); } catch { prompt("Copia el texto:", text); }
      break;
    }
  }
});

$("#hosts").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const h = hostsCache.find((x) => x.uid === Number(b.dataset.uid));
  if (!h) return;
  if (b.dataset.act === "menu") {
    e.stopPropagation();
    if (!rowMenu.hidden && rowMenu.dataset.uid === String(h.uid)) closeRowMenu();
    else openRowMenu(b, h);
  } else if (b.dataset.act === "edit") {
    openDevice(h, b.dataset.focus);
  }
});

// ---- exportar / importar CSV -----------------------------------------------------------------

function downloadCsv(macs = []) {
  const a = document.createElement("a");
  a.href = "/api/hosts/export.csv" + (macs.length ? `?macs=${encodeURIComponent(macs.join(","))}` : "");
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

$("#host-export").addEventListener("click", () => downloadCsv());
$("#host-import").addEventListener("click", () => $("#host-import-file").click());

const FIELD_NAMES = { nombre: "Nombre", ip_fija: "IP fija", tipo: "Tipo", grupos: "Grupos", notas: "Notas", conocido: "Conocido" };
let importText = "";

function fmtImportValue(v) {
  if (Array.isArray(v)) return v.length ? v.join(", ") : "(ninguno)";
  if (typeof v === "boolean") return v ? "sí" : "no";
  return v === "" ? "(vacío)" : v;
}

$("#host-import-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  if (file.size > 1_000_000) { alert("El archivo es demasiado grande (máximo 1 MB)"); return; }
  importText = await file.text();
  let plan;
  if (!await act(async () => { plan = await api("/api/hosts/import/preview", { method: "POST", body: { csv: importText } }); })) return;
  const n = plan.changes.length;
  $("#import-summary").innerHTML = `<p><b>${esc(file.name)}</b>: ${plan.rows} filas, columnas que se aplican: ${plan.columns.map((c) => FIELD_NAMES[c]).join(", ") || "ninguna"}.</p>` +
    `<p>${n ? `<b>${n}</b> dispositivo(s) con cambios.` : "No hay nada que cambiar: todo coincide con lo que ya tienes."}</p>` +
    [...plan.errors, ...plan.skipped].map((m) => `<p class="notice warn small">${esc(m)}</p>`).join("");
  $("#import-changes").innerHTML = n ? `<table><thead><tr><th>Dispositivo</th><th>Campo</th><th>Ahora</th><th>Pasará a</th></tr></thead><tbody>` +
    plan.changes.flatMap((c) => Object.entries(c.fields).map(([k, [old, nw]], i) =>
      `<tr><td>${i ? "" : `${esc(c.name)}${c.in_router ? "" : ' <span class="badge">no conectado</span>'}`}</td>
        <td>${FIELD_NAMES[k]}${k in { nombre: 1, ip_fija: 1 } ? ' <span class="badge warn">router</span>' : ""}</td>
        <td>${esc(fmtImportValue(old))}</td><td><b>${esc(fmtImportValue(nw))}</b></td></tr>`)).join("") + "</tbody></table>" : "";
  const label = $("#import-router-label");
  label.hidden = !plan.router_changes;
  $("#import-router").checked = false;
  label.querySelector("span").textContent = `Aplicar también ${plan.router_changes} cambio(s) de nombre / IP fija en el router (se hacen de uno en uno; puede tardar)`;
  $("#import-apply").disabled = !n;
  $("#import-dialog").showModal();
});

$("#import-apply").addEventListener("click", async () => {
  const btn = $("#import-apply");
  btn.disabled = true;
  btn.textContent = "Aplicando…";
  let res;
  const ok = await act(async () => {
    res = await api("/api/hosts/import/apply", { method: "POST", body: { csv: importText, include_router: $("#import-router").checked } });
  });
  btn.disabled = false;
  btn.textContent = "Aplicar cambios";
  if (!ok) return;
  $("#import-dialog").close();
  if (res.errors.length) alert(`${res.applied} cambio(s) aplicados. Con error:\n` + res.errors.join("\n"));
  else toast(`${res.applied} cambio(s) aplicados`);
  await loadHosts();
  if (typeof loadGroups === "function") loadGroups().catch(() => {});
});

// ---- preferencias (solo en este navegador) ------------------------------------------------------

const HOST_PREFS = "dashboard.hosts";
try {
  const p = JSON.parse(localStorage.getItem(HOST_PREFS) || "{}");
  if (p.groupBy !== undefined) $("#host-groupby").value = p.groupBy;
  if (p.showInactive !== undefined) $("#show-inactive").checked = p.showInactive;
  if (p.sort) { hostView.sort = p.sort; hostView.dir = p.dir === -1 ? -1 : 1; }
  if (p.filtersVisible) setFiltersVisible(true);
} catch { /* sin almacenamiento: valores por defecto */ }

function saveHostPrefs() {
  try {
    localStorage.setItem(HOST_PREFS, JSON.stringify({
      groupBy: $("#host-groupby").value, showInactive: $("#show-inactive").checked,
      sort: hostView.sort, dir: hostView.dir, filtersVisible: !$("#host-filters").hidden,
    }));
  } catch { /* nada */ }
}

$("#host-search").addEventListener("input", renderHosts);
for (const id of ["#host-groupby", "#show-inactive"]) {
  $(id).addEventListener("change", () => { saveHostPrefs(); renderHosts(); });
}

// ---- ficha de dispositivo ------------------------------------------------------------

const TYPE_PRESETS = ["Móvil", "Ordenador", "Portátil", "Tablet", "Televisión", "Consola", "Altavoz", "Cámara",
  "Enchufe", "Bombilla", "IoT", "Impresora", "NAS", "Repetidor Wi‑Fi", "Reloj", "Otro"];
let deviceEditing = null;
let deviceGroups = new Set();
const dfield = (n) => $("#device-form").elements[n];

function openDevice(h, focus) {
  deviceEditing = h;
  editingUid = h.uid; // que la recarga periódica no cambie la tabla por debajo
  $("#device-title").textContent = h.name;
  $("#device-sub").textContent = [h.mac, h.ip || "sin IP", h.interface, h.detected_type && `detectado como ${h.detected_type}`].filter(Boolean).join(" · ");
  dfield("name").value = h.custom_name ? h.name : "";
  dfield("name").placeholder = h.name;
  dfield("type").value = h.user_type;
  dfield("type").placeholder = h.detected_type || "Móvil, IoT, Televisión…";
  dfield("fixed").checked = !!h.fixed_ip;
  dfield("fixed_ip").value = h.fixed_ip || h.ip || "";
  dfield("notes").value = h.notes;
  deviceGroups = new Set(h.groups);
  $("#device-new-group").value = "";
  const types = [...new Set([...TYPE_PRESETS, ...hostsCache.map((x) => x.type).filter(Boolean)])];
  $("#device-types").innerHTML = types.map((t) => `<option value="${esc(t)}">`).join("");
  drawDeviceGroups();
  updateIpField();
  $("#device-dialog").showModal();
  if (focus === "fixed_ip") (dfield("fixed").checked ? dfield("fixed_ip") : dfield("fixed")).focus();
  else if (focus === "type") dfield("type").focus();
  else if (focus === "groups") $("#device-new-group").focus();
  else dfield("name").focus();
}

function updateIpField() {
  const h = deviceEditing;
  const fixed = dfield("fixed").checked;
  dfield("fixed_ip").disabled = !fixed;
  $("#device-ip-help").textContent = fixed
    ? "El router le dará siempre esta IP. Si es distinta de la actual, la cogerá cuando se reconecte (o al reiniciarlo)."
    : h.fixed_ip
      ? `Al guardar se quitará la IP fija ${h.fixed_ip}: el router le asignará la que quiera.`
      : "Ahora la IP la asigna el router automáticamente y puede cambiar con el tiempo.";
}

function drawDeviceGroups() {
  const names = [...new Set([...allGroupNames(), ...deviceGroups])].sort((a, b) => a.localeCompare(b, "es"));
  $("#device-groups").innerHTML = names.map((g) =>
    `<button type="button" class="chip-toggle${deviceGroups.has(g) ? " on" : ""}" data-g="${esc(g)}" aria-pressed="${deviceGroups.has(g)}">${esc(g)}</button>`).join("") ||
    '<span class="muted small">Aún no hay grupos: crea uno abajo.</span>';
}

function addDeviceGroup() {
  const name = $("#device-new-group").value.trim();
  if (!name) return;
  deviceGroups.add(name);
  $("#device-new-group").value = "";
  drawDeviceGroups();
}

$("#device-groups").addEventListener("click", (e) => {
  const b = e.target.closest("[data-g]");
  if (!b) return;
  deviceGroups.has(b.dataset.g) ? deviceGroups.delete(b.dataset.g) : deviceGroups.add(b.dataset.g);
  drawDeviceGroups();
});
$("#device-add-group").addEventListener("click", addDeviceGroup);
$("#device-new-group").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); addDeviceGroup(); }
});
dfield("fixed").addEventListener("change", () => {
  updateIpField();
  if (dfield("fixed").checked) dfield("fixed_ip").focus();
});
$("#device-form").addEventListener("keydown", (e) => {
  // Enter guarda (salvo en las notas, donde es un salto de línea)
  if (e.key === "Enter" && e.target.tagName === "INPUT" && e.target.id !== "device-new-group") {
    e.preventDefault();
    $("#device-save").click();
  }
});
$("#device-dialog").addEventListener("close", () => { editingUid = null; deviceEditing = null; });

$("#device-save").addEventListener("click", async () => {
  const h = deviceEditing;
  const body = {};
  const name = dfield("name").value.trim();
  if (name !== (h.custom_name ? h.name : "")) body.name = name;
  const fixed = dfield("fixed").checked ? dfield("fixed_ip").value.trim() : "";
  if (dfield("fixed").checked && !fixed) { alert("Escribe la IP que quieres dejar fija"); return; }
  if (fixed !== (h.fixed_ip || "")) body.fixed_ip = fixed;
  const type = dfield("type").value.trim();
  if (type !== h.user_type) body.type = type;
  const notes = dfield("notes").value.trim();
  if (notes !== h.notes) body.notes = notes;
  const groups = [...deviceGroups].sort();
  if (groups.join("\n") !== [...h.groups].sort().join("\n")) body.groups = groups;
  if (!Object.keys(body).length) { $("#device-dialog").close(); return; }
  const btn = $("#device-save");
  btn.disabled = true;
  let res;
  const ok = await act(async () => { res = await api(`/api/hosts/${h.uid}/details`, { method: "PUT", body }); });
  btn.disabled = false;
  if (!ok) return; // la ficha sigue abierta para corregir
  toast(res.message || "Guardado");
  $("#device-dialog").close();
  await loadHosts();
  if (body.groups && typeof loadGroups === "function") loadGroups().catch(() => {});
});

// ---- editor de horario ----------------------------------------------------------------

let scheduleGrid = null;
let scheduleOnSave = null;

// Editor genérico: rojo = "apagado" (sin Internet, Wi-Fi apagada…)
function openScheduleEditor({ title, grid, offLabel = "sin Internet", onLabel = "con Internet", onSave }) {
  scheduleGrid = JSON.parse(JSON.stringify(grid));
  scheduleOnSave = onSave;
  $("#schedule-title").textContent = title;
  $("#legend-off").textContent = offLabel;
  $("#legend-on").textContent = onLabel;
  drawSchedule();
  $("#schedule-dialog").showModal();
}

async function openSchedule(h) {
  let grid;
  try {
    grid = await api(`/api/hosts/${h.uid}/schedule`);
  } catch (err) {
    alert(err.message);
    return;
  }
  openScheduleEditor({
    title: `Horario sin Internet · ${h.name}`,
    grid,
    onSave: async (g) => {
      const ok = await act(() => api(`/api/hosts/${h.uid}/schedule`, { method: "PUT", body: { grid: g } }), "Horario guardado");
      if (ok) {
        loadHosts();
        loadParentalNote().catch(() => {});
      }
      return ok;
    },
  });
}

function drawSchedule() {
  let html = "<tr><th></th>" + Array.from({ length: 24 }, (_, i) => `<th>${i}</th>`).join("") + "</tr>";
  for (const d of DAYS) {
    html += `<tr><th class="day">${DAY_NAMES[d]}</th>` +
      scheduleGrid[d].map((v, h) => `<td data-day="${d}" data-h="${h}" class="${v ? "denied" : ""}" title="${DAY_NAMES[d]} ${h}:00–${h + 1}:00"></td>`).join("") + "</tr>";
  }
  $("#schedule-grid").innerHTML = html;
}

let painting = null; // valor que se está "pintando" al arrastrar
const grid = $("#schedule-grid");
grid.addEventListener("pointerdown", (e) => {
  const td = e.target.closest("td[data-day]");
  if (!td) return;
  e.preventDefault();
  painting = !scheduleGrid[td.dataset.day][td.dataset.h];
  paint(td);
});
grid.addEventListener("pointerover", (e) => {
  const td = e.target.closest("td[data-day]");
  if (td && painting !== null) paint(td);
});
window.addEventListener("pointerup", () => { painting = null; });

function paint(td) {
  scheduleGrid[td.dataset.day][td.dataset.h] = painting;
  td.classList.toggle("denied", painting);
}

document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
  for (const d of DAYS) {
    for (let h = 0; h < 24; h++) {
      const p = b.dataset.preset;
      scheduleGrid[d][h] = p === "all" ? true
        : p === "nights" ? (h >= 23 || h < 7)
        : p === "school" ? (!["SATURDAY", "SUNDAY"].includes(d) && h >= 9 && h < 14)
        : false;
    }
  }
  drawSchedule();
}));

$("#schedule-save").addEventListener("click", async () => {
  if (await scheduleOnSave(scheduleGrid)) $("#schedule-dialog").close();
});

// ---- wifi -------------------------------------------------------------------------------

function signalBars(dbm) {
  if (dbm == null) return "";
  const n = dbm >= -55 ? 4 : dbm >= -65 ? 3 : dbm >= -75 ? 2 : 1;
  const label = ["", "Débil", "Regular", "Buena", "Excelente"][n];
  return `<span class="bars" role="img" aria-label="Señal ${label}, ${dbm} dBm" title="${dbm} dBm · ${label}">${[1, 2, 3, 4].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span>`;
}

let wifiRadiosCache = [];

async function loadWifi() {
  const d = await api("/api/wifi/details");
  wifiRadiosCache = d.radios;
  $("#wifi-networks").innerHTML = d.networks.map((n) => `
    <section class="panel wifi-card" data-alias="${esc(n.alias)}">
      <div class="head">
        <div>
          <h2>${esc(n.ssid)}</h2>
          <div class="muted small">${n.guest ? "Invitados · " : "Principal · "}${esc(n.band)} · ${n.enabled ? '<span class="ok">emitiendo</span>' : "apagada"}</div>
        </div>
        <label class="switch" title="Encender/apagar esta red"><input type="checkbox" data-act="toggle" ${n.enabled ? "checked" : ""} aria-label="Red ${esc(n.ssid)} encendida"><span></span></label>
      </div>
      <div class="field"><label for="ssid-${esc(n.alias)}">Nombre de la red</label><input id="ssid-${esc(n.alias)}" data-f="ssid" value="${esc(n.ssid)}" maxlength="32" autocomplete="off" spellcheck="false"></div>
      <div class="field"><label for="pw-${esc(n.alias)}">Contraseña</label>
        <div class="pw"><input id="pw-${esc(n.alias)}" data-f="password" type="password" value="${esc(n.password)}" maxlength="63" autocomplete="off">
          <button type="button" class="small" data-act="show-pw">Ver</button></div></div>
      <label class="inline"><input type="checkbox" data-f="visible" ${n.visible ? "checked" : ""}> Visible (si la desmarcas, la red no aparece en la lista y hay que escribir el nombre a mano)</label>
      <div class="row end">
        <button type="button" data-act="qr">Código QR</button>
        <button type="button" class="primary" data-act="save">Guardar cambios</button>
      </div>
      <h3>Conectados ahora (${n.clients.filter((c) => c.active).length})</h3>
      <ul class="clients">${n.clients.map((c) => `<li><span>${esc(c.name)}${c.active ? "" : ' <span class="muted small">(inactivo)</span>'}</span>
          <span class="muted small">${signalBars(c.signal_dbm)} ${c.down_mbps ? `${c.down_mbps} Mbit/s` : ""}</span></li>`).join("") || '<li class="muted">Nadie</li>'}</ul>
    </section>`).join("");

  $("#radios").innerHTML = d.radios.map((r) => `
    <div class="radio-box" data-radio="${esc(r.alias)}">
      <strong>${esc(r.band)}</strong>
      <div class="muted small">Canal actual ${r.channel ?? "?"} · ancho ${esc(r.bandwidth)} · ${esc(r.standards)}</div>
      <div class="row" style="margin-top:8px">
        <select data-act="channel" aria-label="Canal ${esc(r.band)}">
          <option value="auto" ${r.auto_channel ? "selected" : ""}>Automático</option>
          ${r.possible_channels.map((c) => `<option value="${c}" ${!r.auto_channel && c === r.channel ? "selected" : ""}>Canal ${c}</option>`).join("")}
        </select>
        <button class="small" data-act="save-channel">Aplicar</button>
      </div>
    </div>`).join("");
}

$("#wifi-networks").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const card = b.closest(".wifi-card");
  const alias = card.dataset.alias;
  if (b.dataset.act === "show-pw") {
    const inp = card.querySelector('[data-f="password"]');
    inp.type = inp.type === "password" ? "text" : "password";
    b.textContent = inp.type === "password" ? "Ver" : "Ocultar";
  } else if (b.dataset.act === "qr") {
    $("#qr-title").textContent = card.querySelector("h2").textContent;
    $("#qr-img").src = `/api/wifi/${encodeURIComponent(alias)}/qr.svg?t=${Date.now()}`;
    $("#qr-dialog").showModal();
  } else if (b.dataset.act === "save") {
    const body = {
      ssid: card.querySelector('[data-f="ssid"]').value,
      password: card.querySelector('[data-f="password"]').value,
      visible: card.querySelector('[data-f="visible"]').checked,
    };
    if (!confirm("Al cambiar el nombre o la contraseña, todos los aparatos conectados a esta red se desconectarán y tendrás que volver a conectarlos con los datos nuevos. ¿Continuar?")) return;
    if (await act(() => api(`/api/wifi/${encodeURIComponent(alias)}`, { method: "PATCH", body }), "Wi‑Fi actualizada")) loadWifi();
  }
});

$("#wifi-networks").addEventListener("change", async (e) => {
  const input = e.target.closest('input[data-act="toggle"]');
  if (!input) return;
  const alias = input.closest(".wifi-card").dataset.alias;
  const enabled = input.checked;
  if (!enabled && !confirm("¿Apagar esta red? Si estás conectado por ella perderás la conexión.")) {
    input.checked = true;
    return;
  }
  await act(() => api(`/api/wifi/${encodeURIComponent(alias)}`, { method: "POST", body: { enabled } }), enabled ? "Red encendida" : "Red apagada");
  loadWifi();
});

$("#radios").addEventListener("click", async (e) => {
  const b = e.target.closest('[data-act="save-channel"]');
  if (!b) return;
  const box = b.closest(".radio-box");
  const v = box.querySelector("select").value;
  const channel = v === "auto" ? null : Number(v);
  if (!confirm("Cambiar el canal desconecta unos segundos a todos los aparatos de esa banda. ¿Continuar?")) return;
  if (await act(() => api(`/api/radios/${encodeURIComponent(box.dataset.radio)}/channel`, { method: "POST", body: { channel } }), "Canal cambiado")) loadWifi();
});

// ---- herramientas --------------------------------------------------------------------------

$("#ping-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#ping-out");
  out.hidden = false;
  out.textContent = "Haciendo ping…";
  try {
    const r = await api("/api/ping", { method: "POST", body: { host: $("#ping-host").value.trim() } });
    out.textContent = `${r.host}: ${r.received}/${r.sent} respuestas (${r.loss_pct} % perdidas)` +
      (r.avg_ms != null ? `, ${r.avg_ms} ms de media` : "") + `\n\n${JSON.stringify(r.raw, null, 2)}`;
  } catch (err) {
    out.textContent = err.message;
  }
});

$("#reboot").addEventListener("click", async () => {
  const txt = prompt("El router se reiniciará y toda la casa se quedará sin Internet unos minutos.\nEscribe REINICIAR para confirmar:");
  if (txt !== "REINICIAR") return;
  await act(() => api("/api/reboot", { method: "POST", body: { confirm: "REINICIAR" } }), "Orden de reinicio enviada");
});

// ---- arranque y refresco ------------------------------------------------------------------------

loaders.resumen = () => Promise.all([loadHistory(), loadEvents()]).catch((e) => showError(e.message));
loaders.dispositivos = () => Promise.all([loadHosts(), loadParentalNote()]).then(renderHosts).catch((e) => showError(e.message));
loaders.wifi = () => loadWifi().catch((e) => showError(e.message));

let refreshing = false;

async function refresh() {
  // El router es lento: nunca dos recargas a la vez, y nada con la pestaña oculta
  if (refreshing || document.hidden) return;
  refreshing = true;
  try {
    const jobs = [loadStatus(), editingUid === null ? loadHosts() : Promise.resolve()];
    if (currentTab === "resumen") jobs.push(loadHistory(), loadEvents());
    await Promise.all(jobs);
    showError("");
  } catch (err) {
    showError(err.message);
  } finally {
    refreshing = false;
  }
}

// Al terminar de cargar todos los scripts (cada pestaña registra su loader)
window.addEventListener("load", () => {
  showTab(location.hash.slice(1) || "resumen");
  refresh();
  setInterval(refresh, REFRESH_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
});
