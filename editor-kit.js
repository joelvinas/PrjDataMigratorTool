"use strict";
/* Shared by the Stage 2 and Stage 3 editors: a type-ahead picker, plain inputs and the Markdown text box with a live preview
   (parsed by the server, the same parser the saved record and the reports use). The page script defines markDirty(), which these
   call after any edit. Everything is inserted with textContent, never innerHTML. */

const SAFE_URL = /^(https?:\/\/|mailto:|tel:)[^\s<>"']+$/i;
const previewCache = new Map();   // Markdown text -> parsed preview, so redrawing a tree does not re-ask the server

/* ---------- small widgets ---------- */
/* Type-ahead list. options: [{id, label, sub}]. */
function picker(options, valueId, placeholder, onPick, bad) {
  const wrap = el("div", { class: "combo pick" + (bad ? " bad" : "") });
  const inp = el("input", { type: "text", placeholder, autocomplete: "off", role: "combobox", "aria-expanded": "false" });
  const btn = el("button", { type: "button", "aria-label": "Show the list" }, "▾");
  const menu = el("ul", { class: "combo-menu", role: "listbox" });
  menu.hidden = true;
  const labelOf = (id) => { const o = options.find((x) => x.id === id); return o ? o.label : (id ? id + (bad ? " (not available here)" : "") : ""); };
  let active = -1;
  inp.value = labelOf(valueId);
  const close = () => { menu.hidden = true; inp.setAttribute("aria-expanded", "false"); active = -1; inp.value = labelOf(valueId); };
  const choose = (o) => { valueId = o.id; menu.hidden = true; inp.value = o.label; inp.setAttribute("aria-expanded", "false"); onPick(o.id); };
  function open(filter) {
    clear(menu);
    const f = (filter || "").trim().toLowerCase();
    const list = f ? options.filter((o) => (o.label + " " + o.id + " " + (o.sub || "")).toLowerCase().includes(f)) : options;
    active = -1;
    if (!list.length) menu.appendChild(el("li", { class: "combo-empty", text: options.length ? "Nothing matches." : "No fields available." }));
    list.slice(0, 200).forEach((o) => {
      const li = el("li", { role: "option" }, el("span", { class: "combo-name", text: o.label }), el("code", { text: o.sub || o.id }));
      li.addEventListener("mousedown", (ev) => { ev.preventDefault(); choose(o); });
      li._opt = o;
      menu.appendChild(li);
    });
    menu.hidden = false;
    inp.setAttribute("aria-expanded", "true");
  }
  inp.addEventListener("focus", () => { inp.select(); open(""); });
  inp.addEventListener("input", () => open(inp.value));
  inp.addEventListener("blur", close);
  inp.addEventListener("keydown", (ev) => {
    const items = Array.from(menu.querySelectorAll("li[role=option]"));
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (menu.hidden) open("");
      if (!items.length) return;
      active = (active + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((li, i) => li.classList.toggle("active", i === active));
      items[active].scrollIntoView({ block: "nearest" });
    } else if (ev.key === "Enter" && !menu.hidden && active >= 0) { ev.preventDefault(); choose(items[active]._opt); }
    else if (ev.key === "Escape") { close(); inp.blur(); }
  });
  btn.addEventListener("mousedown", (ev) => { ev.preventDefault(); if (menu.hidden) { inp.focus(); } else close(); });
  wrap.append(inp, btn, menu);
  return wrap;
}

function textInput(value, onInput, attrs) {
  const i = el("input", Object.assign({ type: "text", autocomplete: "off" }, attrs || {}));
  i.value = value || "";
  i.addEventListener("input", () => { onInput(i.value); markDirty(); });
  return i;
}
function labelled(text, control, cls) { return el("label", { class: "lbl " + (cls || "") }, el("span", { text }), control); }

/* ---------- rich text (Markdown) editor with live preview from the server's parser ---------- */
function runsNodes(runs) {
  const out = [];
  for (const r of runs) {
    let nodes = [];
    r.t.split("\n").forEach((t, i) => { if (i) nodes.push(el("br")); nodes.push(document.createTextNode(t)); });
    if (r.b) nodes = [el("strong", {}, nodes)];
    if (r.i) nodes = [el("em", {}, nodes)];
    if (r.href && SAFE_URL.test(r.href)) nodes = [el("a", { href: r.href, target: "_blank", rel: "noopener noreferrer" }, nodes), el("span", { class: "url", text: " (" + r.href + ")" })];
    out.push(...nodes);
  }
  return out;
}
function renderPreview(box, p) {
  clear(box);
  for (const b of p.blocks) {
    if (b.type === "p") box.appendChild(el("p", {}, runsNodes(b.runs)));
    else box.appendChild(el(b.type, {}, b.items.map((it) => el("li", {}, runsNodes(it)))));
  }
  box.hidden = !p.blocks.length;
}
function richBox(label, getV, setV, rows) {
  const ta = el("textarea", { rows: rows || 3, "aria-label": label, spellcheck: "true" });
  ta.value = getV() || "";
  const preview = el("div", { class: "rich-preview" });
  const warn = el("div", { class: "msg err" });
  let timer = null;
  const refresh = (now) => {
    clearTimeout(timer);
    if (!ta.value.trim()) { clear(preview); preview.hidden = true; warn.textContent = ""; return; }
    const show = (p) => {
      renderPreview(preview, p);
      warn.textContent = p.warnings.length ? "Problem: " + p.warnings.join("; ") + ". Links must start with http://, https://, mailto: or tel:." : "";
    };
    if (previewCache.has(ta.value)) { show(previewCache.get(ta.value)); return; }
    timer = setTimeout(async () => {
      const text = ta.value;
      try {
        const r = await api("POST", "/api/forms/preview", { texts: [text] });
        if (previewCache.size > 500) previewCache.clear();
        previewCache.set(text, r.previews[0]);
        if (ta.value === text) show(r.previews[0]);
      } catch (e) { warn.textContent = e.message; }
    }, now ? 0 : 300);
  };
  ta.addEventListener("input", () => { setV(ta.value); markDirty(); refresh(); });
  const edit = (fn) => { fn(); ta.dispatchEvent(new Event("input")); ta.focus(); };
  const wrapSel = (pre, post, ph) => edit(() => {
    const s = ta.selectionStart, e = ta.selectionEnd;
    ta.setRangeText(pre + (ta.value.slice(s, e) || ph) + post, s, e, "select");
  });
  const bBold = el("button", { type: "button", class: "tb", title: "Bold", text: "B" });
  bBold.style.fontWeight = "700";
  const bItal = el("button", { type: "button", class: "tb", title: "Italic", text: "I" });
  bItal.style.fontStyle = "italic";
  const bList = el("button", { type: "button", class: "tb", title: "Bulleted list item", text: "• List" });
  const bLink = el("button", { type: "button", class: "tb", title: "Link", text: "Link" });
  bBold.addEventListener("click", () => wrapSel("**", "**", "bold text"));
  bItal.addEventListener("click", () => wrapSel("*", "*", "italic text"));
  bList.addEventListener("click", () => edit(() => {
    const s = ta.selectionStart, pre = s === 0 || ta.value[s - 1] === "\n" ? "" : "\n";
    ta.setRangeText(pre + "- ", s, ta.selectionEnd, "end");
  }));
  const lt = el("input", { type: "text", placeholder: "Text people see", autocomplete: "off" });
  const lu = el("input", { type: "text", placeholder: "Address (https://...)", autocomplete: "off" });
  const lmsg = el("span", { class: "msg err" });
  const lform = el("div", { class: "linkform" }, lt, lu, el("button", { type: "button", class: "tb", text: "Insert" }), el("button", { type: "button", class: "tb", text: "Cancel" }), lmsg);
  lform.hidden = true;
  bLink.addEventListener("click", () => {
    lt.value = ta.value.slice(ta.selectionStart, ta.selectionEnd);
    lu.value = ""; lmsg.textContent = ""; lform.hidden = false; (lt.value ? lu : lt).focus();
  });
  lform.children[2].addEventListener("click", () => {
    const text = lt.value.trim(), url = lu.value.trim();
    if (!text) { lmsg.textContent = "Enter the text people see."; return; }
    if (!SAFE_URL.test(url)) { lmsg.textContent = "The address must start with http://, https://, mailto: or tel: and contain no spaces."; return; }
    const md = "[" + text.replace(/([\\\[\]])/g, "\\$1") + "](" + url + ")";
    lform.hidden = true;
    edit(() => { const s = ta.selectionStart, e = ta.selectionEnd; ta.setRangeText(md, s, e, "end"); });
  });
  lform.children[3].addEventListener("click", () => { lform.hidden = true; });
  refresh(true);
  return el("div", { class: "rich" }, el("div", { class: "rich-label", text: label }),
    el("div", { class: "rich-tools" }, bBold, bItal, bList, bLink), lform, ta, warn, preview);
}

