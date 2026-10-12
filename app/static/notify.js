// ---- avisos al móvil: listas de ntfy con una regla por categoría -------------------------------

const PRIORITIES = { 1: "1 · Mínima (sin sonido)", 2: "2 · Baja", 3: "3 · Normal", 4: "4 · Alta", 5: "5 · Urgente" };
let notifyData = null;   // {categories, lists} del servidor
let notifyCurrent = null; // id de la lista abierta (null = una nueva sin guardar)
let notifyDraft = null;  // copia que se está editando
let notifyDirty = false;

const clone = (o) => JSON.parse(JSON.stringify(o));
const renderTpl = (tpl, values) => String(tpl || "").replace(/\{(\w+)\}/g, (m, k) => (k in values ? values[k] : m));

async function loadNotify() {
  notifyData = await api("/api/notify");
  if (notifyDirty) { renderNotifyTabs(); return; } // no pisar lo que se está editando
  if (!notifyData.lists.some((l) => l.id === notifyCurrent)) notifyCurrent = notifyData.lists[0]?.id ?? null;
  openNotifyList(notifyCurrent);
}

function openNotifyList(id) {
  notifyCurrent = id;
  const lst = notifyData.lists.find((l) => l.id === id);
  notifyDraft = lst ? clone(lst) : null;
  if (notifyDraft) delete notifyDraft.token; // el token nunca viene del servidor; undefined = no tocarlo
  notifyDirty = false;
  renderNotifyTabs();
  renderNotifyEditor();
}

function newNotifyDraft() {
  const rules = Object.fromEntries(Object.entries(notifyData.categories).map(([k, c]) => [k, clone(c.defaults)]));
  const rnd = crypto.getRandomValues(new Uint32Array(2));
  return { id: null, name: "Nueva lista", enabled: false, server: "https://ntfy.sh",
    topic: `router-${rnd[0].toString(36)}${rnd[1].toString(36)}`, has_token: false,
    quiet: { enabled: false, start: 23, end: 8, mode: "silent" }, rules };
}

function renderNotifyTabs() {
  const tabs = notifyData.lists.map((l) => {
    const name = l.id === notifyCurrent && notifyDraft ? notifyDraft.name : l.name;
    return `<button type="button" role="tab" class="list-tab${l.id === notifyCurrent ? " on" : ""}" data-list="${esc(l.id)}" aria-selected="${l.id === notifyCurrent}">
      <span class="dot ${l.enabled ? "on" : ""}"></span>${esc(name)}${l.id === notifyCurrent && notifyDirty ? " *" : ""}</button>`;
  });
  if (notifyDraft && notifyDraft.id === null) tabs.push(`<button type="button" role="tab" class="list-tab on" aria-selected="true">${esc(notifyDraft.name)} *</button>`);
  $("#notify-tabs").innerHTML = tabs.join("");
}

function filterSummary(f) {
  if (!f || f.mode === "all") return "Todos";
  const what = [...f.groups, ...f.types].join(", ");
  return (f.mode === "include" ? "Solo: " : "Excepto: ") + what;
}

function exampleValues(cat) {
  const now = new Date();
  return { hora: now.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }),
    fecha: now.toLocaleDateString("es-ES"), ...notifyData.categories[cat].example };
}

function quietHourOptions(selected) {
  return Array.from({ length: 24 }, (_, h) => `<option value="${h}"${h === selected ? " selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("");
}

function statusLine(st) {
  if (!st) return '<span class="muted">Todavía no se ha enviado nada desde que arrancó el dashboard.</span>';
  const when = fmtTime(st.ts);
  return st.ok
    ? `<span class="ok-text">✓ Último aviso aceptado por ntfy: ${when}${st.title ? ` · «${esc(st.title)}»` : ""}</span>`
    : `<span class="danger-text">✗ Último aviso falló (${when}): ${esc(st.error)}</span>`;
}

function renderNotifyEditor() {
  const ed = $("#notify-editor");
  if (!notifyDraft) {
    ed.innerHTML = '<p class="muted">Aún no tienes ninguna lista. Pulsa <b>+ Nueva lista</b> para crear la primera.</p>';
    return;
  }
  const d = notifyDraft;
  const subscribeUrl = `${d.server.replace(/\/$/, "")}/${encodeURIComponent(d.topic)}`;
  const rows = Object.entries(notifyData.categories).map(([k, c]) => {
    const r = d.rules[k];
    const vars = c.vars.map((v) => `<button type="button" class="var-chip" data-var="${v}" title="Insertar en el mensaje">{${v}}</button>`).join(" ");
    return `<tr data-cat="${k}" class="${r.enabled ? "" : "off"}">
      <td class="c-on"><input type="checkbox" data-r="enabled" ${r.enabled ? "checked" : ""} aria-label="Avisar de ${esc(c.label)}"></td>
      <td class="c-cat"><b>${esc(c.label)}</b><div class="vars">${vars}</div></td>
      <td class="c-title"><input data-r="title" value="${esc(r.title)}" maxlength="100" aria-label="Título"></td>
      <td class="c-msg"><textarea data-r="template" rows="2" maxlength="1000" aria-label="Mensaje">${esc(r.template)}</textarea>
        <div class="preview" data-preview></div></td>
      <td class="c-prio"><select data-r="priority" aria-label="Prioridad">${Object.entries(PRIORITIES).map(([p, l]) =>
        `<option value="${p}"${Number(p) === r.priority ? " selected" : ""}>${l}</option>`).join("")}</select></td>
      <td class="c-tags"><input data-r="tags" value="${esc(r.tags.join(", "))}" placeholder="warning, tada" aria-label="Etiquetas"></td>
      <td class="c-filter">${c.device ? `<button type="button" class="small" data-filter title="¿De qué aparatos avisar?">${esc(filterSummary(r.filter))}</button>` : '<span class="muted">—</span>'}</td>
      <td class="c-test"><button type="button" class="small" data-test title="Enviar un ejemplo de este aviso a la lista">Probar</button></td>
    </tr>`;
  }).join("");
  ed.innerHTML = `
    <div class="form-grid notify-head">
      <label class="inline"><input type="checkbox" data-f="enabled" ${d.enabled ? "checked" : ""}> Lista activada</label>
      <label>Nombre<input data-f="name" value="${esc(d.name)}" maxlength="40"></label>
      <label>Tema de ntfy<span class="row"><input data-f="topic" value="${esc(d.topic)}" maxlength="64" style="flex:1"><button type="button" class="small" data-gen>Generar</button></span></label>
      <label>Servidor<input data-f="server" value="${esc(d.server)}" maxlength="200"></label>
      <label>Token (opcional)<span class="row"><input type="password" data-f="token" autocomplete="off"
        placeholder="${d.has_token ? "Guardado · escribe para cambiarlo" : "Solo si el tema está protegido"}" style="flex:1">
        ${d.has_token ? '<button type="button" class="small" data-clear-token>Quitar</button>' : ""}</span></label>
      <div class="span2 quiet">
        <label class="inline"><input type="checkbox" data-q="enabled" ${d.quiet.enabled ? "checked" : ""}> Horas de silencio</label>
        de <select data-q="start">${quietHourOptions(d.quiet.start)}</select> a <select data-q="end">${quietHourOptions(d.quiet.end)}</select>:
        <select data-q="mode"><option value="silent"${d.quiet.mode === "silent" ? " selected" : ""}>llegan sin sonido</option>
          <option value="skip"${d.quiet.mode === "skip" ? " selected" : ""}>no se envían</option></select>
        <span class="muted small">(los de prioridad 5 · Urgente suenan siempre)</span>
      </div>
    </div>
    <p class="help">Para recibirlos: app ntfy → <b>+</b> → tema <code>${esc(d.topic)}</code>${d.server !== "https://ntfy.sh" ? ` en el servidor ${esc(d.server)}` : ""}
      · <a href="${esc(subscribeUrl)}" target="_blank" rel="noopener">abrir en el navegador</a>.
      El tema es como una contraseña: quien lo conozca verá estos avisos.</p>
    <p class="small" id="notify-status">${statusLine(d.status)}</p>
    <div class="table-wrap"><table class="notify-rules">
      <thead><tr><th>Avisar</th><th>Categoría y variables</th><th>Título</th><th>Mensaje</th><th>Prioridad</th><th>Etiquetas</th><th>Aparatos</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="help">En el mensaje y el título puedes usar las variables de cada fila (pulsa una para insertarla). <code>{mensaje}</code> es el texto normal del dashboard.
      Las etiquetas son emojis de ntfy separados por comas: <code>warning</code> ⚠️, <code>rotating_light</code> 🚨, <code>tada</code> 🎉, <code>lock</code> 🔒, <code>wifi</code>…</p>
    <div class="row end">
      ${d.id ? '<button type="button" class="danger" data-delete>Borrar lista</button>' : ""}
      <button type="button" data-test-all>Enviar prueba</button>
      ${notifyDirty || !d.id ? '<button type="button" data-discard>Descartar cambios</button>' : ""}
      <button type="button" class="primary" data-save>${d.id ? "Guardar" : "Crear lista"}</button>
    </div>`;
  ed.querySelectorAll("tr[data-cat]").forEach(updatePreview);
}

function updatePreview(tr) {
  const cat = tr.dataset.cat;
  const r = notifyDraft.rules[cat];
  const v = exampleValues(cat);
  tr.querySelector("[data-preview]").innerHTML = `<b>${esc(renderTpl(r.title, v))}</b> · ${esc(renderTpl(r.template, v))}`;
  tr.classList.toggle("off", !r.enabled);
}

function markDirty() {
  if (!notifyDirty) {
    notifyDirty = true;
    renderNotifyTabs();
    const actions = $("#notify-editor .row.end");
    if (actions && !actions.querySelector("[data-discard]")) {
      actions.querySelector("[data-save]").insertAdjacentHTML("beforebegin", '<button type="button" data-discard>Descartar cambios</button>');
    }
  }
}

function onNotifyEdit(e) {
  const el = e.target;
  const d = notifyDraft;
  if (!d) return;
  if (el.dataset.f) {
    d[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.f === "name") { markDirty(); renderNotifyTabs(); return; }
  } else if (el.dataset.q) {
    d.quiet[el.dataset.q] = el.type === "checkbox" ? el.checked : el.dataset.q === "mode" ? el.value : Number(el.value);
  } else if (el.dataset.r) {
    const tr = el.closest("tr[data-cat]");
    const r = d.rules[tr.dataset.cat];
    const k = el.dataset.r;
    r[k] = k === "enabled" ? el.checked : k === "priority" ? Number(el.value) : k === "tags" ? el.value.split(",").map((t) => t.trim()).filter(Boolean) : el.value;
    updatePreview(tr);
  } else {
    return;
  }
  markDirty();
}
// "input" para el texto mientras se escribe y "change" para casillas y desplegables
$("#notify-editor").addEventListener("input", onNotifyEdit);
$("#notify-editor").addEventListener("change", onNotifyEdit);

function draftBody() {
  const d = notifyDraft;
  const body = { name: d.name, enabled: d.enabled, server: d.server, topic: d.topic, quiet: d.quiet, rules: d.rules };
  if (d.token !== undefined) body.token = d.token; // "" = quitar; sin campo = no tocar
  return body;
}

async function saveNotify() {
  const d = notifyDraft;
  const saved = d.id
    ? await api(`/api/notify/lists/${encodeURIComponent(d.id)}`, { method: "PUT", body: draftBody() })
    : await api("/api/notify/lists", { method: "POST", body: draftBody() });
  notifyDirty = false;
  notifyData = await api("/api/notify");
  openNotifyList(saved.id);
  return saved;
}

async function sendNotifyTest(kind) {
  // La prueba usa lo guardado: si hay cambios, se guardan antes
  if (notifyDirty || !notifyDraft.id) await saveNotify();
  try {
    return await api(`/api/notify/lists/${encodeURIComponent(notifyDraft.id)}/test`, { method: "POST", body: { kind } });
  } finally {
    // Haya ido bien o mal, mostrar el resultado real del último envío
    notifyData = await api("/api/notify");
    const st = notifyData.lists.find((l) => l.id === notifyDraft.id)?.status;
    notifyDraft.status = st;
    $("#notify-status").innerHTML = statusLine(st);
  }
}

$("#notify-editor").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b || !notifyDraft) return;
  if (b.dataset.var) {
    const ta = b.closest("tr").querySelector("textarea[data-r=template]");
    const pos = ta.selectionStart ?? ta.value.length;
    ta.value = ta.value.slice(0, pos) + `{${b.dataset.var}}` + ta.value.slice(ta.selectionEnd ?? pos);
    ta.focus();
    ta.selectionStart = ta.selectionEnd = pos + b.dataset.var.length + 2;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  } else if ("gen" in b.dataset) {
    const rnd = crypto.getRandomValues(new Uint32Array(2));
    const input = $("#notify-editor [data-f=topic]");
    input.value = `router-${rnd[0].toString(36)}${rnd[1].toString(36)}`;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  } else if ("clearToken" in b.dataset) {
    notifyDraft.token = "";
    b.textContent = "Se quitará al guardar";
    b.disabled = true;
    markDirty();
  } else if ("filter" in b.dataset) {
    openNotifyFilter(b.closest("tr").dataset.cat);
  } else if ("test" in b.dataset) {
    const cat = b.closest("tr").dataset.cat;
    let res;
    if (await act(async () => { res = await sendNotifyTest(cat); })) toast(res.message);
  } else if ("testAll" in b.dataset) {
    let res;
    if (await act(async () => { res = await sendNotifyTest(null); })) toast(res.message);
  } else if ("save" in b.dataset) {
    if (await act(saveNotify)) toast("Lista guardada");
  } else if ("discard" in b.dataset) {
    notifyDirty = false;
    openNotifyList(notifyData.lists[0] && !notifyDraft.id ? notifyData.lists[0].id : notifyDraft.id);
  } else if ("delete" in b.dataset) {
    if (!confirm(`¿Borrar la lista «${notifyDraft.name}»? Quien esté suscrito dejará de recibir avisos.`)) return;
    if (await act(() => api(`/api/notify/lists/${encodeURIComponent(notifyDraft.id)}`, { method: "DELETE" }), "Lista borrada")) {
      notifyDirty = false;
      notifyCurrent = null;
      await loadNotify();
    }
  }
});

$("#notify-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-list]");
  if (!b || b.dataset.list === notifyCurrent) return;
  if (notifyDirty && !confirm("Hay cambios sin guardar en esta lista. ¿Descartarlos?")) return;
  openNotifyList(b.dataset.list);
});

$("#notify-new").addEventListener("click", () => {
  if (notifyDirty && !confirm("Hay cambios sin guardar. ¿Descartarlos?")) return;
  notifyCurrent = null;
  notifyDraft = newNotifyDraft();
  notifyDirty = true;
  renderNotifyTabs();
  renderNotifyEditor();
  $("#notify-editor [data-f=name]").select();
});

// ---- filtro de aparatos de una regla ----------------------------------------------------------------

let filterCat = null;

async function openNotifyFilter(cat) {
  filterCat = cat;
  if (!hostsCache.length) await loadHosts().catch(() => {});
  let groups = allGroupNames();
  try { groups = [...new Set([...groups, ...(await api("/api/extras/groups")).map((g) => g.name)])].sort((a, b) => a.localeCompare(b, "es")); } catch { /* sin grupos */ }
  const types = [...new Set([...TYPE_PRESETS, ...hostsCache.map((h) => h.type), ...hostsCache.map((h) => h.detected_type)].filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, "es"));
  const f = clone(notifyDraft.rules[cat].filter);
  const chips = (list, sel, kind) => list.map((v) =>
    `<button type="button" class="chip-toggle${sel.includes(v) ? " on" : ""}" data-kind="${kind}" data-v="${esc(v)}" aria-pressed="${sel.includes(v)}">${esc(v)}</button>`).join("") ||
    '<span class="muted small">Ninguno todavía</span>';
  $("#nf-title").textContent = `¿De qué aparatos avisar? · ${notifyData.categories[cat].label}`;
  $("#nf-groups").innerHTML = chips(groups, f.groups, "groups");
  $("#nf-types").innerHTML = chips(types, f.types, "types");
  document.querySelector(`#notify-filter-dialog input[name=mode][value=${f.mode}]`).checked = true;
  $("#nf-pickers").hidden = f.mode === "all";
  $("#notify-filter-dialog").showModal();
}

$("#notify-filter-dialog").addEventListener("change", (e) => {
  if (e.target.name === "mode") $("#nf-pickers").hidden = e.target.value === "all";
});
$("#notify-filter-dialog").addEventListener("click", (e) => {
  const b = e.target.closest(".chip-toggle");
  if (!b) return;
  b.classList.toggle("on");
  b.setAttribute("aria-pressed", b.classList.contains("on"));
});
$("#nf-ok").addEventListener("click", () => {
  const mode = document.querySelector("#notify-filter-dialog input[name=mode]:checked").value;
  const pick = (kind) => [...document.querySelectorAll(`#notify-filter-dialog .chip-toggle.on[data-kind=${kind}]`)].map((b) => b.dataset.v);
  const f = { mode, groups: mode === "all" ? [] : pick("groups"), types: mode === "all" ? [] : pick("types") };
  if (mode !== "all" && !f.groups.length && !f.types.length) { alert("Marca al menos un grupo o un tipo"); return; }
  notifyDraft.rules[filterCat].filter = f;
  $("#notify-filter-dialog").close();
  const btn = document.querySelector(`#notify-editor tr[data-cat="${filterCat}"] [data-filter]`);
  if (btn) btn.textContent = filterSummary(f);
  markDirty();
});

const _loadAutomationsTab = loaders.automatizaciones;
loaders.automatizaciones = () => Promise.all([_loadAutomationsTab(), loadNotify()]).catch((e) => showError(e.message));
