"use strict";
// Extras: informe, gráfica de la fibra, grupos, Wi-Fi de invitados temporal, avisos al móvil y editor de automatizaciones.

// ---- informe y fibra (Resumen) -----------------------------------------------------------

async function loadReport() {
  const days = Number($("#report-days").value);
  const r = await api(`/api/extras/report?days=${days}`);
  const maxDay = Math.max(...r.per_day.map((d) => d.down_gb + d.up_gb), 0.01);
  $("#report").innerHTML = `
    <div class="cards report-cards">
      <div><h3>Descargado</h3><div class="big">${r.total_down_gb} GB</div><div class="muted small">subido ${r.total_up_gb} GB</div></div>
      <div><h3>Caídas</h3><div class="big ${r.outages ? "bad" : "ok"}">${r.outages}</div><div class="muted small">${r.downtime_min} min sin Internet</div></div>
      <div><h3>Pico</h3><div class="big">${r.peak_down_mbps}</div><div class="muted small">Mbit/s de bajada</div></div>
      <div><h3>Nuevos</h3><div class="big">${r.new_devices.length}</div><div class="muted small">aparatos${r.intruders.length ? ` · <span class="bad">${r.intruders.length} de madrugada</span>` : ""}</div></div>
    </div>
    ${r.per_day.length > 1 ? `<div class="bars-chart" title="Consumo por día">${r.per_day.map((d) => `
      <div class="bar-col" title="${esc(d.day)}: ↓ ${d.down_gb} GB · ↑ ${d.up_gb} GB">
        <div class="bar" style="height:${(100 * (d.down_gb + d.up_gb)) / maxDay}%"></div>
        <span>${new Date(d.day).toLocaleDateString("es-ES", { weekday: "short" })}</span></div>`).join("")}</div>` : ""}
    <p class="muted small">Datos de ${r.coverage_hours} h (solo mientras el dashboard estaba encendido).</p>`;
}

async function loadFiberChart() {
  const days = Number($("#report-days").value);
  const rows = (await api(`/api/history?hours=${days * 24}`)).filter((r) => r.rx_dbm != null);
  const el = $("#fiber-chart");
  if (rows.length < 2) {
    el.innerHTML = '<p class="muted">Aún no hay suficientes datos.</p>';
    return;
  }
  const W = el.clientWidth || 500, H = el.clientHeight || 160, padL = 50, padB = 18, padT = 6;
  const vals = rows.map((r) => r.rx_dbm);
  const lo = Math.min(-28, ...vals) - 0.5, hi = Math.max(-17, ...vals) + 0.5;
  const t0 = rows[0].ts, t1 = rows[rows.length - 1].ts;
  const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (W - padL - 4);
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  let grid = "";
  for (const v of [Math.ceil(hi), -20, -27].filter((v, i, a) => a.indexOf(v) === i && v <= hi && v >= lo)) {
    grid += `<line class="grid" x1="${padL}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${padL - 6}" y="${y(v) + 4}" text-anchor="end">${v} dBm</text>`;
  }
  const bad = `<rect x="${padL}" y="${y(-27)}" width="${W - padL}" height="${Math.max(0, H - padB - y(-27))}" fill="var(--danger)" opacity=".08"/>`;
  const path = rows.map((r, i) => `${i ? "L" : "M"}${x(r.ts).toFixed(1)},${y(r.rx_dbm).toFixed(1)}`).join("");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bad}${grid}<path d="${path}" fill="none" stroke="var(--rx)" stroke-width="1.6"/></svg>`;
}

$("#report-days").addEventListener("change", () => Promise.all([loadReport(), loadFiberChart()]).catch((e) => showError(e.message)));

const _loadResumen = loaders.resumen;
loaders.resumen = () => Promise.all([_loadResumen(), loadReport(), loadFiberChart()]).catch((e) => showError(e.message));

// ---- grupos (Dispositivos) -------------------------------------------------------------------

let groupsData = [];

async function loadGroups() {
  groupsData = await api("/api/extras/groups");
  $("#groups").innerHTML = groupsData.map((g) => {
    const blocked = g.members.filter((m) => m.block === "always").length;
    return `<div class="group" data-group="${esc(g.name)}">
      <div><b>${esc(g.name)}</b> <span class="muted small">${g.members.map((m) => esc(m.name)).join(", ")}</span>
        ${blocked ? `<span class="pill blocked">${blocked}/${g.members.length} bloqueados</span>` : ""}</div>
      <div class="actions">
        <select class="small" data-gact="block" aria-label="Bloquear grupo"><option value="">Bloquear…</option>
          <option value="30">30 minutos</option><option value="60">1 hora</option><option value="120">2 horas</option><option value="always">Hasta desbloquear</option></select>
        <button class="small" data-gact="unblock">Desbloquear</button>
        <button class="small" data-gact="schedule">Horario</button>
        <button class="small" data-gact="edit">Editar</button>
        <button class="small danger" data-gact="delete">Borrar</button>
      </div></div>`;
  }).join("") || '<p class="muted small">Aún no tienes grupos.</p>';
  if (typeof fillGroupFilter === "function") fillGroupFilter();
}

function groupEditor(name = "", macs = []) {
  const gid = `group-name-${Math.random().toString(36).slice(2, 8)}`;
  const opts = hostsCache.map((h) => `<label class="inline"><input type="checkbox" value="${esc(h.mac)}" ${macs.includes(h.mac) ? "checked" : ""}> ${esc(h.name)} <span class="muted small">${esc(h.ip)}</span></label>`).join("");
  $("#groups").insertAdjacentHTML("afterbegin", `<form class="group-edit radio-box" data-old="${esc(name)}">
    <div class="field"><label for="${gid}">Nombre del grupo</label><input id="${gid}" name="name" value="${esc(name)}" maxlength="40" required autocomplete="off"></div>
    <div class="group-members">${opts}</div>
    <div class="row end"><button type="button" class="small" data-gact="cancel">Cancelar</button><button class="small primary">Guardar grupo</button></div>
  </form>`);
}

$("#group-new").addEventListener("click", () => groupEditor());

$("#groups").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const name = f.name.value.trim();
  const macs = [...f.querySelectorAll(".group-members input:checked")].map((i) => i.value);
  const ok = await act(async () => {
    await api(`/api/extras/groups/${encodeURIComponent(name)}`, { method: "PUT", body: { macs } });
    if (f.dataset.old && f.dataset.old !== name) await api(`/api/extras/groups/${encodeURIComponent(f.dataset.old)}`, { method: "DELETE" });
  }, "Grupo guardado");
  if (ok) { loadGroups(); loadHosts(); }
});

$("#groups").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-gact]");
  if (!b) return;
  if (b.dataset.gact === "cancel") { b.closest("form").remove(); return; }
  const name = b.closest("[data-group]").dataset.group;
  const g = groupsData.find((x) => x.name === name);
  const url = `/api/extras/groups/${encodeURIComponent(name)}`;
  switch (b.dataset.gact) {
    case "unblock":
      if (await act(() => api(`${url}/block`, { method: "POST", body: { blocked: false } }), `${name} desbloqueado`)) { loadGroups(); loadHosts(); }
      break;
    case "edit": groupEditor(name, g.macs); break;
    case "delete":
      if (confirm(`¿Borrar el grupo ${name}? (no desbloquea a sus aparatos)`) && await act(() => api(url, { method: "DELETE" }))) { loadGroups(); loadHosts(); }
      break;
    case "schedule":
      openScheduleEditor({
        title: `Horario sin Internet · grupo ${name}`,
        grid: Object.fromEntries(DAYS.map((d) => [d, Array(24).fill(false)])),
        onSave: async (grid) => {
          if (!confirm(`Se aplicará este horario a los ${g.members.length} aparatos del grupo, sustituyendo el que tengan. ¿Continuar?`)) return false;
          const ok = await act(() => api(`${url}/schedule`, { method: "PUT", body: { grid } }), "Horario aplicado al grupo");
          if (ok) { loadGroups(); loadHosts(); }
          return ok;
        },
      });
      break;
  }
});

$("#groups").addEventListener("change", async (e) => {
  const sel = e.target.closest('select[data-gact="block"]');
  if (!sel || !sel.value) return;
  const name = sel.closest("[data-group]").dataset.group;
  const minutes = sel.value === "always" ? null : Number(sel.value);
  if (!confirm(minutes ? `¿Quitar Internet al grupo ${name} durante ${minutes} minutos?` : `¿Quitar Internet al grupo ${name} hasta desbloquearlo?`)) { sel.value = ""; return; }
  if (await act(() => api(`/api/extras/groups/${encodeURIComponent(name)}/block`, { method: "POST", body: { blocked: true, minutes } }), `${name} bloqueado`)) {
    loadGroups(); loadHosts();
  }
  sel.value = "";
});

const _loadDevices = loaders.dispositivos;
loaders.dispositivos = () => Promise.all([_loadDevices(), loadGroups()]).catch((e) => showError(e.message));

// ---- Wi-Fi de invitados temporal ------------------------------------------------------------

async function loadGuestTemp() {
  const g = await api("/api/extras/guest");
  const el = $("#guest-temp");
  if (g.active) {
    el.innerHTML = `<div class="guest-on">
      <img src="/api/wifi/${encodeURIComponent(g.aliases[0])}/qr.svg?t=${Date.now()}" alt="QR de la red de invitados" width="150" height="150">
      <div><p><span class="pill">Activa</span> hasta las <b>${hhmm(g.until)}</b></p>
        <p>Red: <b>${esc(g.ssid)}</b><br>Contraseña: <b class="mono">${esc(g.password)}</b></p>
        <button id="guest-stop" class="danger small">Apagar ya</button></div></div>`;
  } else {
    el.innerHTML = `<form id="guest-form" class="row wrap">
      <select name="hours" aria-label="Duración"><option value="1">1 hora</option><option value="3" selected>3 horas</option><option value="6">6 horas</option><option value="24">1 día</option><option value="72">3 días</option></select>
      <label class="inline"><input type="checkbox" name="new_password" checked> Contraseña nueva</label>
      <button class="primary">Activar Wi‑Fi de invitados</button></form>`;
  }
}

$("#guest-temp").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { hours: Number(f.hours.value), new_password: f.new_password.checked, bands: ["2g", "5g"] };
  if (await act(() => api("/api/extras/guest", { method: "POST", body }), "Wi‑Fi de invitados activada")) { loadGuestTemp(); loadWifi().catch(() => {}); }
});

$("#guest-temp").addEventListener("click", async (e) => {
  if (e.target.id !== "guest-stop") return;
  if (await act(() => api("/api/extras/guest", { method: "DELETE" }), "Wi‑Fi de invitados apagada")) { loadGuestTemp(); loadWifi().catch(() => {}); }
});

const _loadWifiTab = loaders.wifi;
loaders.wifi = () => Promise.all([_loadWifiTab(), loadGuestTemp()]).catch((e) => showError(e.message));

// ---- editor de automatizaciones ----------------------------------------------------------------------

const ACTION_HELP = {
  wifi: { label: "Encender/apagar una red Wi‑Fi", params: { alias: "WL_GUEST2G4", enabled: false } },
  reboot: { label: "Reiniciar el router", params: {} },
  ping_watchdog: { label: "Vigilar Internet con ping (y reiniciar si falla)", params: { host: "1.1.1.1", count: 4, reboot_on_fail: false } },
  block_group: { label: "Bloquear un grupo", params: { group: "Niños", minutes: 60 } },
  unblock_group: { label: "Desbloquear un grupo", params: { group: "Niños" } },
  guest_wifi: { label: "Encender Wi‑Fi de invitados temporal", params: { hours: 3, new_password: true } },
  speedtest: { label: "Test de velocidad", params: {} },
};
let autoItems = [];
let autoActions = [];

async function loadAutomations() {
  const [items, actions] = await Promise.all([api("/api/automations"), api("/api/automations/actions")]);
  autoItems = items;
  autoActions = actions;
  renderAutomations();
}

function renderAutomations() {
  $("#automations").innerHTML = autoItems.map((a, i) => `
    <div class="auto-item radio-box" data-i="${i}">
      <div class="row wrap">
        <label class="switch" title="Activar/desactivar"><input type="checkbox" data-f="enabled" ${a.enabled ? "checked" : ""} aria-label="Automatización activada"><span></span></label>
        <input data-f="name" value="${esc(a.name)}" placeholder="Nombre" aria-label="Nombre de la automatización" style="flex:1;min-width:160px">
        <input data-f="cron" value="${esc(a.cron)}" placeholder="0 23 * * *" class="mono" style="width:130px" aria-label="Horario cron">
        <select data-f="action">${autoActions.map((k) => `<option value="${k}" ${k === a.action ? "selected" : ""}>${esc((ACTION_HELP[k] || {}).label || k)}</option>`).join("")}</select>
      </div>
      <div class="row wrap mt">
        <textarea data-f="params" rows="1" spellcheck="false" aria-label="Parámetros JSON">${esc(JSON.stringify(a.params || {}))}</textarea>
      </div>
      <div class="row wrap end">
        <span class="muted small" style="margin-right:auto">${a.next_run ? `Próxima: ${new Date(a.next_run).toLocaleString("es-ES")}` : a.enabled ? "Se programará al guardar" : "Desactivada"}</span>
        ${a.id ? `<button type="button" class="small" data-aact="run">Ejecutar ahora</button>` : ""}
        <button type="button" class="small danger" data-aact="delete">Quitar</button>
      </div>
    </div>`).join("") || '<p class="muted">No hay automatizaciones. Pulsa "+ Nueva".</p>';
}

function collectAutomations() {
  return [...document.querySelectorAll(".auto-item")].map((el) => {
    const a = autoItems[Number(el.dataset.i)];
    const q = (f) => el.querySelector(`[data-f="${f}"]`);
    let params;
    try {
      params = JSON.parse(q("params").value || "{}");
    } catch {
      throw new Error(`Parámetros no válidos en "${q("name").value}": deben ser JSON, p. ej. {"alias": "WL_GUEST2G4", "enabled": false}`);
    }
    return { id: a.id, name: q("name").value, cron: q("cron").value, action: q("action").value, enabled: q("enabled").checked, params };
  });
}

$("#automations").addEventListener("change", (e) => {
  // Al cambiar de acción, poner parámetros de ejemplo
  const sel = e.target.closest('[data-f="action"]');
  if (!sel) return;
  const ta = sel.closest(".auto-item").querySelector('[data-f="params"]');
  ta.value = JSON.stringify((ACTION_HELP[sel.value] || {}).params || {});
});

$("#automations").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-aact]");
  if (!b) return;
  const i = Number(b.closest(".auto-item").dataset.i);
  if (b.dataset.aact === "delete") {
    autoItems = collectAutomations().filter((_, j) => j !== i);
    renderAutomations();
  } else if (b.dataset.aact === "run" && confirm("¿Ejecutar esta automatización ahora (con la versión guardada)?")) {
    await act(async () => toast((await api(`/api/automations/${encodeURIComponent(autoItems[i].id)}/run`, { method: "POST" })).message));
  }
});

$("#auto-add").addEventListener("click", () => {
  try { autoItems = collectAutomations(); } catch { /* se valida al guardar */ }
  autoItems.push({ name: "Nueva automatización", cron: "0 23 * * *", action: "wifi", params: ACTION_HELP.wifi.params, enabled: false });
  renderAutomations();
});

$("#auto-save").addEventListener("click", async () => {
  let items;
  try {
    items = collectAutomations();
  } catch (err) {
    alert(err.message);
    return;
  }
  if (await act(() => api("/api/automations", { method: "PUT", body: items }), "Automatizaciones guardadas y programadas")) loadAutomations();
});

loaders.automatizaciones = () => loadAutomations().catch((e) => showError(e.message));
