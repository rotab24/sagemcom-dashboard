"use strict";
// Herramientas: diagnóstico, test de velocidad, registro, copia de seguridad, hora, DNS dinámico,
// acceso remoto, syslog, contraseña y restablecer. (Ping y reinicio están en app.js.)

let maintData = null;
let logLines = [];

async function loadMaintenance() {
  maintData = await api("/api/maintenance");
  const m = maintData;
  $("#fw-info").textContent = `Firmware ${m.info.firmware} (${m.info.internal_firmware}) · compilado ${String(m.info.build_date || "").slice(0, 10)} · ${m.info.reboot_count} reinicios`;
  $("#time-info").textContent = `Hora del router: ${m.time.local_time ? new Date(m.time.local_time).toLocaleString("es-ES") : "?"} · ${m.time.status === "SYNCHRONIZED" ? "sincronizada ✔" : m.time.status}`;
  const ntp = $("#ntp-form");
  ntp.s1.value = m.time.servers[0] || "";
  ntp.s2.value = m.time.servers[1] || "";

  const dd = $("#dyndns-form");
  dd.service.innerHTML = m.dyndns.services.map((s) => `<option>${esc(s)}</option>`).join("");
  dd.enabled.checked = m.dyndns.enabled;
  dd.service.value = m.dyndns.service;
  dd.hostname.value = m.dyndns.hostname;
  dd.username.value = m.dyndns.username;
  $("#dyndns-status").textContent = m.dyndns.enabled ? `· ${m.dyndns.status}${m.dyndns.last_ip && m.dyndns.last_ip !== "0.0.0.0" ? ` (${m.dyndns.last_ip})` : ""}` : "";

  const ra = $("#remote-form");
  for (const k of ["http", "https", "ssh", "telnet"]) ra[k].checked = m.remote_access[k];
  $("#trusted-list").textContent = m.remote_access.trusted.join(", ") || "sin restricción";

  const sl = $("#syslog-form");
  sl.enabled.checked = m.syslog.enabled;
  sl.ip.value = m.syslog.ip;
  sl.port.value = m.syslog.port;

  $("#router-user").textContent = m.user;
  renderSpeedtest(m.speedtest);
}

function renderSpeedtest(st) {
  const out = $("#speedtest-out");
  if (!st.history.length && !st.last.download) {
    out.innerHTML = '<p class="muted small">Aún no hay mediciones.</p>';
    return;
  }
  const l = st.last;
  out.innerHTML = `
    <div class="speed">
      <div><h3>Bajada</h3><div class="big">${l.download ?? "—"} <span class="small">Mbit/s</span></div></div>
      <div><h3>Subida</h3><div class="big">${l.upload ?? "—"} <span class="small">Mbit/s</span></div></div>
      <div><h3>Latencia</h3><div class="big">${l.latency ?? "—"} <span class="small">ms</span></div></div>
    </div>
    ${st.history.length > 1 ? `<details class="mt"><summary>Historial (${st.history.length})</summary><ul class="list">${st.history.map((h) =>
      `<li><span>${esc(new Date(h.time).toLocaleString("es-ES"))}</span><span>↓ ${h.download ?? "?"} · ↑ ${h.upload ?? "?"} Mbit/s · ${h.latency ?? "?"} ms</span></li>`).join("")}</ul></details>` : ""}`;
}

function diagHost() {
  return $("#ping-host").value.trim();
}

$("#traceroute-btn").addEventListener("click", async () => {
  const out = $("#ping-out");
  out.hidden = false;
  out.textContent = "Trazando la ruta… (hasta un minuto)";
  try {
    const r = await api("/api/maintenance/traceroute", { method: "POST", body: { host: diagHost() } });
    out.textContent = `Ruta hasta ${r.host}:\n\n` + (r.hops.map((h) => `${String(h.n).padStart(2)}  ${h.host}${h.ip && h.ip !== h.host ? ` (${h.ip})` : ""}  ${h.ms ? h.ms + " ms" : ""}`).join("\n") || JSON.stringify(r.raw, null, 2));
  } catch (err) {
    out.textContent = err.message;
  }
});

$("#nslookup-btn").addEventListener("click", async () => {
  const out = $("#ping-out");
  out.hidden = false;
  out.textContent = "Consultando DNS…";
  try {
    const r = await api("/api/maintenance/nslookup", { method: "POST", body: { host: diagHost() } });
    out.textContent = `${r.host}:\n\n${JSON.stringify(r.result, null, 2)}`;
  } catch (err) {
    out.textContent = err.message;
  }
});

$("#speedtest-btn").addEventListener("click", async () => {
  const b = $("#speedtest-btn");
  if (!confirm("Durante el test (≈1 minuto) Internet irá más lento en toda la casa. ¿Empezar?")) return;
  b.disabled = true;
  b.textContent = "Midiendo…";
  try {
    renderSpeedtest(await api("/api/maintenance/speedtest", { method: "POST" }));
  } catch (err) {
    alert(err.message);
  } finally {
    b.disabled = false;
    b.textContent = "Medir velocidad";
  }
});

function renderLog() {
  const q = $("#log-filter").value.trim().toLowerCase();
  const out = $("#log-out");
  out.hidden = false;
  const lines = q ? logLines.filter((l) => l.toLowerCase().includes(q)) : logLines;
  out.textContent = lines.join("\n") || "(vacío)";
}

$("#log-btn").addEventListener("click", async () => {
  const out = $("#log-out");
  out.hidden = false;
  out.textContent = "Descargando registro…";
  try {
    logLines = (await api("/api/maintenance/log?lines=500")).lines;
    renderLog();
  } catch (err) {
    out.textContent = err.message;
  }
});
$("#log-filter").addEventListener("input", renderLog);

$("#ntp-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const servers = [e.target.s1.value, e.target.s2.value].filter((s) => s.trim());
  if (await act(() => api("/api/maintenance/ntp", { method: "PUT", body: { servers } }), "Servidores de hora guardados")) loadMaintenance();
});

$("#dyndns-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { enabled: f.enabled.checked, service: f.service.value, hostname: f.hostname.value,
    username: f.username.value, password: f.password.value || null };
  if (await act(() => api("/api/maintenance/dyndns", { method: "PUT", body }), "DNS dinámico guardado")) {
    f.password.value = "";
    loadMaintenance();
  }
});

$("#remote-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { http: f.http.checked, https: f.https.checked, ssh: f.ssh.checked, telnet: f.telnet.checked };
  const risky = (body.ssh && !maintData.remote_access.ssh) || (body.telnet && !maintData.remote_access.telnet);
  if (risky && !confirm("SSH/Telnet dan control total del router. Activarlos sin saber lo que haces es un riesgo de seguridad. ¿Continuar?")) return;
  if ((!body.http && maintData.remote_access.http) || (!body.https && maintData.remote_access.https)) {
    if (!confirm("Si quitas el acceso web remoto, el soporte técnico de MásMóvil no podrá entrar a revisar tu router. ¿Continuar?")) return;
  }
  if (await act(() => api("/api/maintenance/remote-access", { method: "PUT", body }), "Acceso remoto guardado")) loadMaintenance();
});

$("#syslog-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { enabled: f.enabled.checked, ip: f.ip.value.trim(), port: Number(f.port.value || 514) };
  if (await act(() => api("/api/maintenance/syslog", { method: "PUT", body }), "Syslog guardado")) loadMaintenance();
});

$("#password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  if (f.new.value !== f.new2.value) { alert("Las dos contraseñas nuevas no coinciden"); return; }
  if (!confirm("Vas a cambiar la contraseña de acceso al router. Apúntala en un sitio seguro. ¿Continuar?")) return;
  if (await act(() => api("/api/maintenance/password", { method: "POST", body: { old: f.old.value, new: f.new.value } }), "Contraseña cambiada (el dashboard ya usa la nueva)")) f.reset();
});

$("#factory-reset-btn").addEventListener("click", async () => {
  const txt = prompt("Esto BORRA toda tu configuración y deja el router como salió de fábrica (Wi‑Fi y contraseñas de la pegatina).\nToda la casa se quedará sin Internet varios minutos y el dashboard dejará de funcionar hasta que vuelvas a poner la contraseña de la pegatina en .env.\n\nEscribe RESTABLECER para confirmar:");
  if (txt !== "RESTABLECER") return;
  await act(() => api("/api/maintenance/factory-reset", { method: "POST", body: { confirm: txt } }), "Restableciendo… el router se reiniciará");
});

loaders.herramientas = () => loadMaintenance().catch((e) => showError(e.message));

// ---- reiniciar el dashboard ------------------------------------------------------------------
// Solo se da por hecho cuando el servidor que contesta es otro (boot_id distinto).

async function loadSysInfo() {
  const s = await api("/api/system");
  $("#sys-info").textContent = `En marcha desde ${fmtTime(s.started)} · proceso ${s.pid}${s.mock ? " · modo simulado" : ""}`;
  const btn = $("#dash-restart");
  btn.disabled = !s.can_restart;
  btn.title = s.can_restart ? "" : "Solo funciona en Windows, arrancando con iniciar.ps1";
}

$("#dash-restart").addEventListener("click", async () => {
  if (!confirm("¿Reiniciar el dashboard? Tarda unos segundos; el router no se toca.")) return;
  const btn = $("#dash-restart");
  btn.disabled = true;
  btn.textContent = "Reiniciando…";
  const t0 = Date.now();
  let before;
  try {
    before = (await api("/api/system/restart", { method: "POST", body: { confirm: true } })).boot_id;
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
    btn.textContent = "Reiniciar dashboard";
    return;
  }
  toast("Reiniciando el dashboard… espera a que vuelva");
  while (Date.now() - t0 < 90000) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const res = await fetch("/api/system", { cache: "no-store" });
      const s = res.ok ? await res.json() : null;
      if (s && s.boot_id !== before) {
        try { sessionStorage.setItem("dashboard-restarted", String(Math.round((Date.now() - t0) / 1000))); } catch { /* nada */ }
        location.reload();
        return;
      }
    } catch { /* todavía apagado */ }
  }
  alert("El dashboard no ha vuelto en 90 s. Ábrelo con el acceso directo «Router Dashboard» o mira data/server.err.log.");
  btn.disabled = false;
  btn.textContent = "Reiniciar dashboard";
});

window.addEventListener("load", () => {
  let secs = null;
  try { secs = sessionStorage.getItem("dashboard-restarted"); sessionStorage.removeItem("dashboard-restarted"); } catch { /* nada */ }
  if (secs) toast(`✓ Dashboard reiniciado: es un proceso nuevo (tardó ${secs} s)`);
});

const _loadToolsTab = loaders.herramientas;
loaders.herramientas = () => Promise.all([_loadToolsTab(), loadSysInfo()]).catch((e) => showError(e.message));
