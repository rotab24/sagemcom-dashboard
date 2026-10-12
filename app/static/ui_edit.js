// ---- modo edición de la interfaz (botón ✎ o Ctrl+E) -------------------------------------------
// Se carga antes que el resto: envuelve cada texto fijo de la página en <span class="ui-text">
// con una clave estable ("<id de la sección>|<texto original>") y aplica los textos guardados
// en data/texts.json. Los datos que pintan los demás scripts (tablas, listas…) no se tocan.

(() => {
  const custom = window.UI_CUSTOM || { texts: {}, theme: { light: {}, dark: {}, font_size: null, zoom: null } };
  const texts = custom.texts;
  let theme = custom.theme;
  const SKIP = "script, style, option, select, textarea, title, noscript, [data-noedit], #toast, #error";
  const clean = (s) => s.replace(/\s+/g, " ").trim();
  const scopeOf = (el) => el.closest("[id]")?.id || "page";

  // ---- marcar los textos fijos -----------------------------------------------------------------
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (clean(n.nodeValue) && n.parentElement && !n.parentElement.closest(SKIP)
      ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const n of nodes) {
    const [, lead, core, trail] = n.nodeValue.match(/^(\s*)([\s\S]*?)(\s*)$/);
    const span = document.createElement("span");
    span.className = "ui-text";
    span.dataset.orig = clean(core);
    span.dataset.k = `${scopeOf(n.parentElement)}|${span.dataset.orig}`;
    span.textContent = core;
    const frag = document.createDocumentFragment();
    if (lead) frag.append(lead);
    frag.append(span);
    if (trail) frag.append(trail);
    n.replaceWith(frag);
  }
  document.querySelectorAll("input[placeholder], textarea[placeholder]").forEach((el) => {
    if (el.closest("[data-noedit]")) return;
    el.dataset.phOrig = el.placeholder;
    el.dataset.phk = `${scopeOf(el)}|ph|${clean(el.placeholder)}`;
  });

  function applyText(key, value) {
    document.querySelectorAll(`.ui-text[data-k="${CSS.escape(key)}"]`).forEach((s) => {
      s.textContent = value ?? s.dataset.orig;
      s.classList.toggle("custom", value != null);
    });
    document.querySelectorAll(`[data-phk="${CSS.escape(key)}"]`).forEach((el) => {
      el.placeholder = value ?? el.dataset.phOrig;
      el.classList.toggle("custom-ph", value != null);
    });
  }
  for (const [k, v] of Object.entries(texts)) applyText(k, v);

  // ---- apariencia -------------------------------------------------------------------------------
  // Mismo CSS que genera el servidor (ui_custom.theme_css), para verlo en vivo mientras se cambia
  function themeCss(t) {
    const decl = (d) => Object.entries(d).sort().map(([k, v]) => `${k}:${v}`).join(";");
    const parts = [];
    if (Object.keys(t.light).length) parts.push(`@media not all and (prefers-color-scheme: dark){:root{${decl(t.light)}}}`);
    if (Object.keys(t.dark).length) parts.push(`@media (prefers-color-scheme: dark){:root{${decl(t.dark)}}}`);
    if (t.font_size) parts.push(`:root{--font-size:${t.font_size}px}`);
    if (t.zoom && t.zoom !== 1) parts.push(`body{zoom:${t.zoom}}`);
    return parts.join("\n");
  }
  function styleEl() {
    let el = document.getElementById("ui-theme");
    if (!el) { el = document.createElement("style"); el.id = "ui-theme"; document.head.append(el); }
    return el;
  }

  const COLORS = [
    ["--bg", "Fondo"], ["--surface", "Paneles"], ["--text", "Texto"], ["--muted", "Texto secundario"],
    ["--border", "Bordes"], ["--accent", "Acento (botones, enlaces)"], ["--ok", "Correcto"], ["--warn", "Aviso"],
    ["--danger", "Peligro / bloqueado"], ["--rx", "Gráfica: bajada"], ["--tx", "Gráfica: subida"],
  ];
  const dark = () => matchMedia("(prefers-color-scheme: dark)").matches;
  const toHex = (() => {
    const ctx = document.createElement("canvas").getContext("2d");
    return (c) => { ctx.fillStyle = "#000000"; ctx.fillStyle = c.trim() || "#000000"; return ctx.fillStyle; };
  })();

  // ---- barra de edición ---------------------------------------------------------------------------
  const bar = document.createElement("div");
  bar.id = "ui-bar";
  bar.hidden = true;
  bar.setAttribute("data-noedit", "");
  bar.innerHTML = `
    <div class="ui-bar-main">
      <b>✎ Modo edición</b>
      <span class="muted">Haz clic en un texto para cambiarlo (Enter guarda, Esc cancela; vacío = original) · <kbd>Alt</kbd>+clic = clic normal · <kbd>Ctrl</kbd>+<kbd>E</kbd> para salir</span>
      <span class="ui-bar-actions">
        <button type="button" class="small" data-ui="theme" aria-expanded="false">Apariencia</button>
        <button type="button" class="small" data-ui="reset-texts">Restablecer textos</button>
        <button type="button" class="small primary" data-ui="exit">Salir</button>
      </span>
    </div>
    <div class="ui-theme-panel" hidden>
      <p class="muted small" data-ui-scheme></p>
      <div class="ui-colors"></div>
      <div class="ui-sizes">
        <label>Tamaño del texto <input type="range" min="11" max="22" step="1" data-ui-size="font_size"> <output></output></label>
        <label>Escala de la interfaz <input type="range" min="0.7" max="1.6" step="0.05" data-ui-size="zoom"> <output></output></label>
        <button type="button" class="small" data-ui="reset-theme">Restablecer apariencia</button>
      </div>
    </div>`;
  document.body.append(bar);
  const panel = bar.querySelector(".ui-theme-panel");

  function notify(msg) { (typeof toast === "function" ? toast : alert)(msg); }

  async function send(method, path, body) {
    const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!res.ok) {
      const d = (await res.json().catch(() => ({}))).detail;
      throw new Error(Array.isArray(d) ? d.map((e) => e.msg).join(", ") : d || `HTTP ${res.status}`);
    }
    return res.json();
  }

  function renderThemePanel() {
    const scheme = dark() ? "dark" : "light";
    const cs = getComputedStyle(document.documentElement);
    bar.querySelector("[data-ui-scheme]").textContent =
      `Colores del modo ${scheme === "dark" ? "oscuro" : "claro"} (el que usa ahora tu sistema; el otro se guarda aparte).`;
    bar.querySelector(".ui-colors").innerHTML = COLORS.map(([v, label]) => {
      const val = theme[scheme][v] || toHex(cs.getPropertyValue(v));
      return `<label class="${theme[scheme][v] ? "changed" : ""}"><input type="color" data-ui-color="${v}" value="${val}"> ${label}</label>`;
    }).join("");
    const fs = bar.querySelector("[data-ui-size=font_size]");
    fs.value = theme.font_size || 14;
    fs.nextElementSibling.textContent = `${fs.value} px`;
    const z = bar.querySelector("[data-ui-size=zoom]");
    z.value = theme.zoom || 1;
    z.nextElementSibling.textContent = `${Math.round(z.value * 100)} %`;
  }

  let saveTimer;
  function themeChanged() {
    styleEl().textContent = themeCss(theme);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        theme = await send("PUT", "/api/ui/theme", theme);
        notify("Apariencia guardada");
      } catch (err) { alert(err.message); }
    }, 500);
  }

  bar.addEventListener("input", (e) => {
    const el = e.target;
    if (el.dataset.uiColor) {
      const scheme = dark() ? "dark" : "light";
      theme[scheme] = { ...theme[scheme], [el.dataset.uiColor]: el.value };
      el.closest("label").classList.add("changed");
      themeChanged();
    } else if (el.dataset.uiSize) {
      const v = Number(el.value);
      theme[el.dataset.uiSize] = el.dataset.uiSize === "zoom" ? (v === 1 ? null : v) : (v === 14 ? null : v);
      el.nextElementSibling.textContent = el.dataset.uiSize === "zoom" ? `${Math.round(v * 100)} %` : `${v} px`;
      themeChanged();
    }
  });

  bar.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-ui]");
    if (!b) return;
    switch (b.dataset.ui) {
      case "exit": setEditing(false); break;
      case "theme": {
        panel.hidden = !panel.hidden;
        b.setAttribute("aria-expanded", String(!panel.hidden));
        if (!panel.hidden) renderThemePanel();
        break;
      }
      case "reset-texts":
        if (!confirm("¿Volver a los textos originales en toda la interfaz?")) return;
        try {
          await send("DELETE", "/api/ui/texts");
          for (const k of Object.keys(texts)) { applyText(k, null); delete texts[k]; }
          notify("Textos restablecidos");
        } catch (err) { alert(err.message); }
        break;
      case "reset-theme":
        if (!confirm("¿Volver a los colores y tamaños originales (modo claro y oscuro)?")) return;
        try {
          await send("DELETE", "/api/ui/theme");
          theme = { light: {}, dark: {}, font_size: null, zoom: null };
          styleEl().textContent = "";
          renderThemePanel();
          notify("Apariencia restablecida");
        } catch (err) { alert(err.message); }
        break;
    }
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!panel.hidden) renderThemePanel(); });

  // ---- editar un texto ------------------------------------------------------------------------------
  let editing = false;
  let current = null; // {key, input, target}

  function setEditing(on) {
    if (!on && current) finishEdit(true);
    editing = on;
    document.body.classList.toggle("ui-editing", on);
    bar.hidden = !on;
    document.querySelectorAll("[data-ui-toggle]").forEach((b) => b.setAttribute("aria-pressed", String(on)));
  }

  function startEdit(target, isPlaceholder) {
    if (current) finishEdit(true);
    const key = isPlaceholder ? target.dataset.phk : target.dataset.k;
    const value = isPlaceholder ? target.placeholder : target.textContent;
    const r = target.getBoundingClientRect();
    const cs = getComputedStyle(target);
    const long = value.length > 60 || r.height > parseFloat(cs.lineHeight || "20") * 1.6;
    const input = document.createElement(long ? "textarea" : "input");
    input.className = "ui-inline-editor";
    input.value = value;
    input.setAttribute("data-noedit", "");
    Object.assign(input.style, {
      left: `${Math.max(8, r.left + scrollX - 4)}px`, top: `${r.top + scrollY - 3}px`,
      width: `${Math.min(innerWidth - 16, Math.max(r.width + 40, 160))}px`,
      font: cs.font, fontSize: cs.fontSize,
    });
    if (long) input.rows = Math.min(8, Math.max(2, Math.ceil(value.length / 70)));
    document.body.append(input);
    current = { key, input, target };
    input.focus();
    input.select();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !(long && e.shiftKey)) { e.preventDefault(); finishEdit(true); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finishEdit(false); }
    });
    input.addEventListener("blur", () => finishEdit(true));
  }

  async function finishEdit(save) {
    if (!current) return;
    const { key, input, target } = current;
    current = null;
    input.remove();
    if (!save) return;
    const orig = target.dataset.orig ?? target.dataset.phOrig;
    let value = clean(input.value);
    if (!value || value === clean(orig)) value = null; // vacío o igual que el original = sin personalizar
    if (value === (texts[key] ?? null)) return;
    const before = texts[key] ?? null;
    applyText(key, value);
    try {
      await send("PUT", "/api/ui/text", { key, value });
      if (value == null) delete texts[key]; else texts[key] = value;
      notify(value == null ? "Texto original restablecido" : "Texto guardado");
    } catch (err) {
      applyText(key, before);
      alert(`No se pudo guardar: ${err.message}`);
    }
  }

  // Captura: en modo edición, un clic en un texto lo edita en vez de pulsar el botón, enlace…
  document.addEventListener("click", (e) => {
    if (!editing || e.altKey || e.target.closest("#ui-bar, .ui-inline-editor")) return;
    const span = e.target.closest(".ui-text");
    const ph = !span && e.target.matches?.("[data-phk]") ? e.target : null;
    if (!span && !ph) return;
    e.preventDefault();
    e.stopPropagation();
    startEdit(span || ph, !!ph);
  }, true);

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "e") {
      e.preventDefault();
      setEditing(!editing);
    } else if (e.key === "Escape" && editing && !current && !document.querySelector("dialog[open]")) {
      setEditing(false);
    }
  }, true);

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-ui-toggle]")) setEditing(!editing);
  });
})();
