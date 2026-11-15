"use strict";
/* The Postman test guide: static text from /api/postman/guide, shown as five cards. Nothing here calls Workfront or touches a token. */

function copyButton(text, label) {
  const b = el("button", { type: "button", class: "copy", text: label || "Copy" });
  b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch (e) { b.textContent = "Select and copy by hand"; }
    setTimeout(() => { b.textContent = label || "Copy"; }, 1500);
  });
  return b;
}

function stepView(test, s, k) {
  const body = s.body === null || s.body === undefined ? null : JSON.stringify(s.body, null, 2);
  const url = "https://{{customer_domain}}/maestro/api" + s.path;
  const kids = [
    el("div", { class: "row" }, el("strong", { text: "Step " + k + (s.optional ? " (optional)" : "") + ": " + s.label })),
    el("div", { class: "row" }, el("span", { class: "tag run", text: s.method }), el("code", { text: url }), copyButton(url, "Copy URL")),
  ];
  if (s.note) kids.push(el("p", { class: "hint", text: s.note }));
  if (body !== null) kids.push(el("div", { class: "row" }, el("span", { class: "hint", text: "Body (JSON)" }), copyButton(body, "Copy body")), el("pre", { class: "code", text: body }));
  if (s.expect) kids.push(el("p", {}, el("strong", { text: "Look for: " }), s.expect));
  if (s.save) kids.push(el("p", { class: "hint", text: "The collection saves the new ID as " + s.save + "." }));
  return el("li", { class: "step" }, kids);
}

function testCard(d, t) {
  const cleanup = t.cleanup.map((c) => el("li", {}, el("span", { class: "tag run", text: c[0] }), " ", el("code", { text: "https://{{customer_domain}}/maestro/api" + c[1] })));
  return el("details", { class: "card", id: "test" + t.id, open: t.id === 1 ? "" : null },
    el("summary", {}, el("h2", { style: "display:inline" }, el("span", { class: "num", text: t.id }), " " + t.title)),
    el("p", {}, el("strong", { text: "Why: " }), t.purpose),
    el("p", {}, el("strong", { text: "Before you start" })), el("ul", {}, t.before.map((b) => el("li", { text: b }))),
    el("p", {}, el("strong", { text: "Steps" })), el("ol", {}, t.steps.map((s, i) => stepView(t, s, i + 1))),
    el("p", {}, el("strong", { text: "How to read the result" })),
    el("div", { class: "scroll" }, el("table", { class: "grid compact" }, el("thead", {}, el("tr", {}, [el("th", { text: "If you see" }), el("th", { text: "It means" })])),
      el("tbody", {}, t.results.map((r) => el("tr", {}, el("td", { text: r[0] }), el("td", { text: r[1] })))))),
    el("p", {}, el("strong", { text: "Also write down" })), el("ul", {}, t.also_record.map((b) => el("li", { text: b }))),
    el("p", {}, el("strong", { text: "Clean up when you finish" }), el("span", { class: "hint", text: " (a delete is permanent; deleting a record type deletes its records)" })), el("ul", {}, cleanup));
}

async function load() {
  const d = await api("GET", "/api/postman/guide");
  $("baseUrl").textContent = d.base_url;
  $("orderNote").textContent = d.order_note;
  clear($("rules")); d.rules.forEach((r) => $("rules").appendChild(el("li", { text: r })));
  const vb = $("varTable").querySelector("tbody"); clear(vb);
  d.variables.forEach((v) => vb.appendChild(el("tr", {}, el("td", {}, el("code", { text: v.name })), el("td", { text: v.help + (v.value ? " (already filled in)" : "") }))));
  const box = $("tests"); clear(box);
  d.tests.forEach((t) => box.appendChild(testCard(d, t)));
}

const HOW = { postman: "Postman", postman_partly: "Postman and by hand", manual: "By hand in the product" };

async function loadSandbox() {
  const d = await api("GET", "/api/sandbox-plan");
  $("sandboxNote").textContent = d.note;
  const box = $("sandbox"); clear(box);
  d.items.forEach((s) => box.appendChild(el("details", { class: "card", id: "sandbox" + s.id },
    el("summary", {}, el("strong", { text: s.id + ": " + s.title }), " ", el("span", { class: "tag run", text: s.system }), " ", el("span", { class: "tag", text: HOW[s.how] || s.how }), s.covered_by ? el("span", { class: "hint", text: " (" + s.covered_by + ")" }) : null),
    el("p", {}, el("strong", { text: "Why: " }), s.why), el("ol", {}, s.steps.map((x) => el("li", { text: x }))),
    el("p", {}, el("strong", { text: "Result: " }), s.result), el("p", {}, el("strong", { text: "It decides: " }), s.decides))));
}

startPage("postman", {}, () => { load().catch((e) => showGlobalError(e.message)); loadSandbox().catch((e) => showGlobalError(e.message)); });
