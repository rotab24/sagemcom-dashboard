"use strict";
// Pestaña "Teléfono y USB".

let phoneData = null;
let usbData = null;

const FEATURE_LABELS = {
  call_waiting: "Llamada en espera (oír un pitido si te llaman mientras hablas)",
  do_not_disturb: "No molestar (el teléfono no suena)",
  block_anonymous: "Rechazar llamadas de número oculto",
  hide_my_number: "Ocultar mi número al llamar",
  caller_id: "Mostrar quién llama (identificador)",
};
const FORWARDS = [
  ["forward_always", "forward_always_number", "Desviar siempre"],
  ["forward_busy", "forward_busy_number", "Desviar si comunica"],
  ["forward_no_answer", "forward_no_answer_number", "Desviar si no contesto"],
];

function fmtDuration(s) {
  if (!s) return "";
  const m = Math.floor(s / 60);
  return m ? `${m} min ${s % 60} s` : `${s} s`;
}

async function loadPhone() {
  phoneData = await api("/api/phone");
  if (!phoneData.available) {
    $("#phone-lines").innerHTML = '<p class="muted">Este router no tiene telefonía.</p>';
    $("#call-logs").innerHTML = "";
    return;
  }
  $("#phone-lines").innerHTML = phoneData.lines.map((l) => `
    <form class="phone-line" data-uid="${l.uid}">
      <p><b>${esc(l.number || l.name)}</b> · ${l.registered ? '<span class="ok">Registrada</span>' : `<span class="bad">${esc(l.reason || l.status)}</span>`}
        ${l.call_state && l.call_state !== "IDLE" ? `· <span class="muted">${esc(l.call_state)}</span>` : ""}</p>
      ${Object.entries(FEATURE_LABELS).map(([k, label]) =>
        `<label class="inline"><input type="checkbox" name="${k}" ${l.features[k] ? "checked" : ""}> ${label}</label>`).join("")}
      <div class="form-grid mt">
        ${FORWARDS.map(([flag, num, label]) => `
          <label><span><input type="checkbox" name="${flag}" ${l.features[flag] ? "checked" : ""}> ${label}</span>
            <input name="${num}" value="${esc(l.features[num])}" placeholder="Número de destino" inputmode="tel"></label>`).join("")}
      </div>
      <div class="row end">
        <button type="button" class="small" data-act="ring">Probar timbre</button>
        <button class="small primary">Guardar</button>
      </div>
    </form>`).join("") || '<p class="muted">No hay líneas configuradas.</p>';

  $("#call-logs").innerHTML = phoneData.call_logs.map((c) => `<li>
      <span>${/in/i.test(c.direction) ? "↙" : /out/i.test(c.direction) ? "↗" : "•"} <b class="mono">${esc(c.number || "Oculto")}</b>
        <span class="muted small">${c.start ? esc(new Date(c.start).toLocaleString("es-ES")) : ""} ${fmtDuration(c.duration)}</span></span>
      <button class="icon-btn" data-call-del="${c.uid}" title="Borrar">✕</button></li>`).join("") || '<li class="muted">Sin llamadas</li>';
  $("#calls-clear").hidden = !phoneData.call_logs.length;
}

async function loadUsb() {
  usbData = await api("/api/usb");
  const u = usbData;
  const vols = u.volumes.map((v) => `<li><span><b>${esc(v.name || "Disco")}</b> <span class="muted small">${esc(v.filesystem)} · ${esc(v.status)}</span></span>
      <span class="small">${v.used_pct != null ? `${v.used_pct} % usado de ${(v.capacity_mb / 1024).toFixed(1)} GB` : ""}</span></li>`).join("");
  $("#usb-info").innerHTML = `
    <ul class="list">${u.ports.map((p) => `<li><span>Puerto ${esc(p.name)} (USB ${esc(p.standard)})</span>
      ${p.sysfs ? `<button class="small" data-eject="${esc(p.sysfs)}">Expulsar</button>` : '<span class="muted small">vacío</span>'}</li>`).join("")}</ul>
    ${u.devices.length ? `<h3 class="mt">Conectado</h3><ul class="list">${u.devices.map((d) => `<li><span>${esc(d.name)}</span><span class="muted small">${esc(d.class)}</span></li>`).join("")}</ul>` : ""}
    ${vols ? `<h3 class="mt">Volúmenes</h3><ul class="list">${vols}</ul>` : '<p class="muted small">No hay ningún disco o pendrive conectado.</p>'}`;
  const f = $("#media-form");
  f.smb.checked = u.smb.enabled;
  f.dlna.checked = u.dlna.enabled;
  f.dlna_name.value = u.dlna.name;
}

$("#phone-lines").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {};
  for (const k of Object.keys(FEATURE_LABELS)) body[k] = f[k].checked;
  for (const [flag, num] of FORWARDS) {
    body[flag] = f[flag].checked;
    body[num] = f[num].value;
  }
  if (await act(() => api(`/api/phone/lines/${f.dataset.uid}`, { method: "PUT", body }), "Línea guardada")) loadPhone();
});

$("#phone-lines").addEventListener("click", async (e) => {
  if (!e.target.closest('[data-act="ring"]')) return;
  if (await act(() => api("/api/phone/ring-test", { method: "POST" }))) alert("Prueba lanzada: el teléfono debería sonar en unos segundos.");
});

$("#call-logs").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-call-del]");
  if (b && await act(() => api(`/api/phone/calls/${b.dataset.callDel}`, { method: "DELETE" }))) loadPhone();
});

$("#calls-clear").addEventListener("click", async () => {
  if (confirm("¿Borrar todo el registro de llamadas?") && await act(() => api("/api/phone/calls", { method: "DELETE" }), "Registro borrado")) loadPhone();
});

$("#usb-info").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-eject]");
  if (!b || !confirm("¿Expulsar el disco? Espera a que no se esté copiando nada.")) return;
  if (await act(() => api("/api/usb/eject", { method: "POST", body: { sysfs: b.dataset.eject } }), "Ya puedes desconectar el disco")) loadUsb();
});

$("#media-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const ok = await act(async () => {
    if (f.smb.checked !== usbData.smb.enabled) await api("/api/usb/smb", { method: "PUT", body: { enabled: f.smb.checked } });
    await api("/api/usb/dlna", { method: "PUT", body: { enabled: f.dlna.checked, name: f.dlna_name.value } });
  }, "Guardado");
  if (ok) loadUsb();
});

loaders.telefono = () => Promise.all([loadPhone(), loadUsb()]).catch((e) => showError(e.message));
