"use strict";
// Wi-Fi avanzada: seguridad, WPS, filtro MAC, aislamiento, límite, horario, radios y redes vecinas.

let advData = null;
const BAND = { RADIO2G4: "2,4 GHz", RADIO5G: "5 GHz", RADIO6G: "6 GHz" };
const MAC_MODES = { DISABLED: "Desactivado (entra cualquiera con la clave)", WHITELIST: "Solo permitir los de la lista", BLACKLIST: "Bloquear los de la lista" };

function emptyGrid() {
  return Object.fromEntries(DAYS.map((d) => [d, Array(24).fill(false)]));
}

async function loadWifiAdvanced() {
  if (!hostsCache.length) await loadHosts();
  advData = await api("/api/wifi/advanced");
  $("#band-steering").checked = advData.band_steering;

  $("#wifi-adv-networks").innerHTML = advData.networks.map((n) => {
    const sched = n.schedule;
    const schedTxt = sched && sched.enabled
      ? `Activo · ${Object.values(sched.grid).flat().filter(Boolean).length} h/semana apagada`
      : "Sin horario";
    return `
    <div class="radio-box adv-card" data-alias="${esc(n.alias)}">
      <strong>${esc(n.ssid)}</strong> <span class="muted small">${esc(BAND[n.radio] || n.radio)}</span>
      <div class="field mt"><label for="sec-${esc(n.alias)}">Seguridad</label>
        <select id="sec-${esc(n.alias)}" data-f="security">${Object.entries(advData.security_modes).map(([k, v]) =>
          `<option value="${k}" ${k === n.security ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
      <label class="inline"><input type="checkbox" data-f="isolation" ${n.isolation ? "checked" : ""}> Aislar clientes (los aparatos de esta red no se ven entre sí)</label>
      <div class="field mt"><label for="maxcl-${esc(n.alias)}">Máximo de aparatos conectados (0 = sin límite)</label>
        <input id="maxcl-${esc(n.alias)}" type="number" inputmode="numeric" min="0" max="128" data-f="max_clients" value="${n.max_clients}"></div>
      <label class="inline"><input type="checkbox" data-f="wps" ${n.wps.enabled ? "checked" : ""} ${n.mac_filter.mode !== "DISABLED" ? "disabled" : ""}> WPS (conectar pulsando un botón)</label>
      <div class="row end">
        <button type="button" class="small" data-act="wps-start" ${n.wps.enabled ? "" : "disabled"}>Pulsar WPS</button>
        <button type="button" class="small primary" data-act="save-ap">Guardar</button>
      </div>

      <details class="mt"><summary>Filtro MAC · ${esc(MAC_MODES[n.mac_filter.mode] ? n.mac_filter.mode === "DISABLED" ? "desactivado" : n.mac_filter.mode === "WHITELIST" ? "solo permitidos" : "bloqueados" : n.mac_filter.mode)} (${n.mac_filter.list.length})</summary>
        <p class="help">Decide qué aparatos pueden conectarse a esta red por su MAC. En "Solo permitir", <b>incluye tu propio móvil/ordenador</b> o te quedarás fuera.</p>
        <select data-f="mac_mode">${Object.entries(MAC_MODES).map(([k, v]) => `<option value="${k}" ${k === n.mac_filter.mode ? "selected" : ""}>${v}</option>`).join("")}</select>
        <ul class="clients mac-list">${n.mac_filter.list.map((m) => `<li><span>${esc(m.name || m.mac)} <span class="muted small mono">${esc(m.mac)}</span></span>
          <button type="button" class="icon-btn" data-act="mac-del" data-mac="${esc(m.mac)}" title="Quitar" aria-label="Quitar de la lista"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-close"/></svg></button></li>`).join("")}</ul>
        <div class="row wrap">
          <select data-f="mac_add" aria-label="Añadir dispositivo"><option value="">+ Añadir dispositivo…</option>
            ${hostsCache.filter((h) => !n.mac_filter.list.some((m) => m.mac === h.mac)).map((h) => `<option value="${esc(h.mac)}">${esc(h.name)} (${esc(h.mac)})</option>`).join("")}</select>
          <input data-f="mac_manual" placeholder="o escribe una MAC" class="mono" style="max-width:170px">
        </div>
        <div class="row end"><button type="button" class="small primary" data-act="save-mac">Aplicar filtro</button></div>
      </details>

      <details class="mt"><summary>Horario de encendido · ${schedTxt}</summary>
        <p class="help">Apaga esta red automáticamente a ciertas horas. Tu router no trae esta función, así que la hace el dashboard: <b>solo funciona mientras el dashboard esté encendido</b>.</p>
        <div class="row wrap">
          <button type="button" class="small" data-act="edit-sched">Editar horario</button>
          ${sched && sched.enabled ? '<button type="button" class="small" data-act="disable-sched">Desactivar horario</button>' : ""}
        </div>
      </details>
    </div>`;
  }).join("");

  $("#wifi-adv-radios").innerHTML = advData.radios.map((r) => `
    <div class="radio-box" data-radio="${esc(r.alias)}">
      <strong>${esc(BAND[r.alias] || r.alias)}</strong>
      <div class="muted small">${esc(r.standards)} · ruido ${r.noise_dbm ?? "?"} dBm · ${r.errors} errores</div>
      <div class="form-grid mt">
        <label>Ancho de canal<select data-f="bandwidth">${r.bandwidths.map((b) => {
          const v = b.toUpperCase() === "AUTO" ? "AUTO" : `${b}MHZ`;
          return `<option value="${v}" ${v === r.bandwidth ? "selected" : ""}>${b.toUpperCase() === "AUTO" ? "Automático" : `${b} MHz`}</option>`;
        }).join("")}</select></label>
        <label>Potencia<select data-f="tx_power">${r.tx_power_options.map((p) =>
          `<option value="${p}" ${p === r.tx_power ? "selected" : ""}>${p === -1 ? "Automática" : `${p} %`}</option>`).join("")}</select></label>
      </div>
      <div class="row end"><button type="button" class="small primary" data-act="save-radio">Aplicar</button></div>
    </div>`).join("");
}

$("#wifi-adv-networks").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const card = b.closest(".adv-card");
  const alias = card.dataset.alias;
  const n = advData.networks.find((x) => x.alias === alias);
  const q = (f) => card.querySelector(`[data-f="${f}"]`);

  if (b.dataset.act === "save-ap") {
    const body = { security: q("security").value, isolation: q("isolation").checked,
      max_clients: Number(q("max_clients").value || 0), wps_enabled: q("wps").checked };
    if (body.security === "NONE" && !confirm("Una red abierta deja que cualquiera se conecte y vea tu tráfico. ¿Seguro?")) return;
    if (body.security !== n.security && !confirm("Cambiar la seguridad desconecta a todos los aparatos de esta red unos segundos. ¿Continuar?")) return;
    if (await act(() => api(`/api/wifi/advanced/ap/${alias}`, { method: "PUT", body }), "Guardado")) loadWifiAdvanced();
  } else if (b.dataset.act === "wps-start") {
    if (await act(() => api(`/api/wifi/advanced/ap/${alias}/wps`, { method: "POST" }))) {
      alert("WPS activado durante 2 minutos: pulsa ahora el botón WPS del aparato que quieras conectar.");
    }
  } else if (b.dataset.act === "mac-del") {
    b.closest("li").remove();
  } else if (b.dataset.act === "save-mac") {
    const macs = [...card.querySelectorAll(".mac-list [data-mac]")].map((x) => x.dataset.mac);
    const add = q("mac_add").value || q("mac_manual").value.trim();
    if (add) macs.push(add);
    const mode = q("mac_mode").value;
    if (mode === "WHITELIST" && !confirm("Solo podrán conectarse a esta red los aparatos de la lista. ¿Has incluido el tuyo?")) return;
    if (await act(() => api(`/api/wifi/advanced/ap/${alias}/macfilter`, { method: "PUT", body: { mode, macs } }), "Filtro aplicado")) loadWifiAdvanced();
  } else if (b.dataset.act === "edit-sched") {
    openScheduleEditor({
      title: `Horario de ${n.ssid}`,
      grid: n.schedule ? n.schedule.grid : emptyGrid(),
      offLabel: "Wi‑Fi apagada", onLabel: "Wi‑Fi encendida",
      onSave: async (grid) => {
        const ok = await act(() => api(`/api/wifi/advanced/schedule/${alias}`, { method: "PUT", body: { enabled: true, grid } }), "Horario guardado");
        if (ok) { loadWifiAdvanced(); loadWifi().catch(() => {}); }
        return ok;
      },
    });
  } else if (b.dataset.act === "disable-sched") {
    if (await act(() => api(`/api/wifi/advanced/schedule/${alias}`, { method: "PUT", body: { enabled: false, grid: n.schedule.grid } }), "Horario desactivado")) loadWifiAdvanced();
  }
});

// Añadir MAC: se muestra en la lista al momento (se guarda con "Aplicar filtro")
$("#wifi-adv-networks").addEventListener("change", (e) => {
  const sel = e.target.closest('[data-f="mac_add"]');
  if (!sel || !sel.value) return;
  const opt = sel.selectedOptions[0];
  const li = document.createElement("li");
  li.innerHTML = `<span>${esc(opt.textContent)}</span><button type="button" class="icon-btn" data-act="mac-del" data-mac="${esc(sel.value)}" title="Quitar" aria-label="Quitar de la lista"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-close"/></svg></button>`;
  sel.closest("details").querySelector(".mac-list").appendChild(li);
  opt.remove();
  sel.value = "";
});

$("#wifi-adv-radios").addEventListener("click", async (e) => {
  const b = e.target.closest('[data-act="save-radio"]');
  if (!b) return;
  const box = b.closest("[data-radio]");
  const body = { bandwidth: box.querySelector('[data-f="bandwidth"]').value, tx_power: Number(box.querySelector('[data-f="tx_power"]').value) };
  if (!confirm("Los aparatos de esta banda se desconectarán unos segundos. ¿Aplicar?")) return;
  if (await act(() => api(`/api/wifi/advanced/radio/${box.dataset.radio}`, { method: "PUT", body }), "Radio actualizada")) loadWifiAdvanced();
});

$("#band-steering").addEventListener("change", async (e) => {
  const enabled = e.target.checked;
  const msg = enabled
    ? "Con band steering el router decide si cada aparato usa 2,4 o 5 GHz. Para que funcione bien, ambas redes deberían tener el mismo nombre y contraseña. ¿Activar?"
    : "¿Desactivar band steering?";
  if (!confirm(msg)) { e.target.checked = !enabled; return; }
  await act(() => api("/api/wifi/advanced/bandsteering", { method: "PUT", body: { enabled } }), enabled ? "Band steering activado" : "Band steering desactivado");
});

document.querySelectorAll("[data-scan]").forEach((b) => b.addEventListener("click", async () => {
  const out = $("#scan-result");
  document.querySelectorAll("[data-scan]").forEach((x) => { x.disabled = true; });
  out.innerHTML = '<p class="muted">Escaneando… (unos 20 segundos)</p>';
  try {
    const r = await api(`/api/wifi/advanced/scan/${b.dataset.scan}`, { method: "POST" });
    const current = (wifiRadiosCache || []).find((x) => x.alias === b.dataset.scan);
    const best = r.advice.best;
    out.innerHTML = `
      <p class="mt">${r.networks.length} redes encontradas. Canal más libre: <b>${best ?? "?"}</b>${current ? ` (ahora usas el ${current.channel}${current.channel === best ? ", que ya es el mejor" : ""})` : ""}.</p>
      <div class="table-wrap"><table><thead><tr><th>Red</th><th>Canal</th><th>Señal</th></tr></thead><tbody>
      ${r.networks.map((n) => `<tr><td>${esc(n.ssid)}</td><td>${n.channel || "?"}</td><td>${signalBars(n.signal_dbm)} <span class="muted small">${n.signal_dbm ?? "?"} dBm</span></td></tr>`).join("")}
      </tbody></table></div>`;
  } catch (err) {
    out.innerHTML = `<p class="bad">${esc(err.message)}</p>`;
  } finally {
    document.querySelectorAll("[data-scan]").forEach((x) => { x.disabled = false; });
  }
}));

// Al abrir la pestaña Wi-Fi se cargan también los ajustes avanzados
const _loadWifiBasic = loaders.wifi;
loaders.wifi = () => Promise.all([_loadWifiBasic(), loadWifiAdvanced()]).catch((e) => showError(e.message));
