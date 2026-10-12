"use strict";
// Explorador de xpaths + modo experto (escritura). Usa api/esc/act/toast de app.js.

let xpathValue;

// Nombre del elemento de una colección: Hosts -> Host, SSIDs -> SSID, PortMappings -> PortMapping
function elementName(collection) {
  if (/ies$/.test(collection)) return collection.replace(/ies$/, "y");
  if (/(ss|us)$/.test(collection)) return collection;
  return collection.replace(/s$/, "");
}

async function readXpath(path) {
  const input = $("#xpath");
  if (path !== undefined) input.value = path;
  path = input.value.trim().replace(/\/+$/, "");
  const out = $("#xpath-out");
  const chips = $("#xpath-children");
  out.hidden = false;
  out.textContent = "Leyendo…";
  chips.innerHTML = "";
  try {
    const r = await api(`/api/xpath?path=${encodeURIComponent(path)}`);
    xpathValue = r.value;
    out.textContent = JSON.stringify(r.value, null, 2);
    const last = path.split("/").pop().replace(/\[.*\]$/, "");
    let children = [];
    if (Array.isArray(r.value)) {
      const el = elementName(last);
      children = r.value.filter((x) => x && typeof x === "object" && x.uid != null)
        .map((x) => [`${el}[@uid="${x.uid}"]${x.Alias ? ` · ${x.Alias}` : ""}`, `${path}/${el}[@uid="${x.uid}"]`]);
    } else if (r.value && typeof r.value === "object") {
      children = Object.entries(r.value).filter(([, v]) => v && typeof v === "object")
        .map(([k, v]) => [`${k}${Array.isArray(v) ? ` [${v.length}]` : ""}`, `${path}/${k}`]);
    }
    chips.innerHTML = children.map(([label, p]) => `<button type="button" class="chip" data-path="${esc(p)}">${esc(label)}</button>`).join("");
  } catch (err) {
    xpathValue = undefined;
    out.textContent = err.message;
  }
}

$("#xpath-form").addEventListener("submit", (e) => { e.preventDefault(); readXpath(); });
$("#xpath-children").addEventListener("click", (e) => {
  const b = e.target.closest("[data-path]");
  if (b) readXpath(b.dataset.path);
});
$("#xpath-up").addEventListener("click", () => {
  const parts = $("#xpath").value.trim().split("/");
  if (parts.length > 1) readXpath(parts.slice(0, -1).join("/"));
});

$("#expert-mode").addEventListener("change", (e) => {
  if (e.target.checked && !confirm("El modo experto escribe directamente en el router sin comprobaciones. Un valor equivocado puede dejarte sin Internet o sin Wi‑Fi. ¿Activarlo?")) {
    e.target.checked = false;
    return;
  }
  $("#expert-panel").hidden = !e.target.checked;
});

$("#expert-load").addEventListener("click", () => {
  $("#expert-value").value = xpathValue === undefined ? "" : JSON.stringify(xpathValue, null, 2);
});

function parseExpertValue() {
  const raw = $("#expert-value").value.trim();
  try {
    return JSON.parse(raw);
  } catch {
    // Texto sin comillas: se toma como cadena
    return raw;
  }
}

// Devuelve el texto de confirmación que espera el servidor, o null si se cancela
async function expertConfirm(path, what) {
  const d = await api(`/api/expert/danger?path=${encodeURIComponent(path)}`);
  if (d.danger) {
    const txt = prompt(`⚠ Ruta delicada: ${d.danger}.\n${what}\n\nUn error aquí puede dejarte sin Internet o sin acceso al router.\nEscribe ${d.phrase} para continuar:`);
    return txt === d.phrase ? txt : null;
  }
  return confirm(`${what}\n\n¿Continuar?`) ? "yes" : null;
}

async function expertOp(kind) {
  const path = $("#xpath").value.trim();
  if (!path) return;
  const value = kind === "delete" ? undefined : parseExpertValue();
  const what = kind === "set" ? `Escribir en ${path}:\n${JSON.stringify(value)}`
    : kind === "add" ? `Añadir dentro de ${path}:\n${JSON.stringify(value)}`
    : `BORRAR ${path}`;
  const confirmText = await expertConfirm(path, what);
  if (!confirmText) return;
  const ok = await act(() => api(`/api/expert/${kind}`, { method: "POST", body: { path, value, confirm: confirmText } }),
    kind === "set" ? "Valor escrito" : kind === "add" ? "Elemento añadido" : "Elemento borrado");
  if (ok) readXpath(kind === "delete" ? path.split("/").slice(0, -1).join("/") : path);
}

$("#expert-set").addEventListener("click", () => expertOp("set"));
$("#expert-add").addEventListener("click", () => expertOp("add"));
$("#expert-delete").addEventListener("click", () => expertOp("delete"));
