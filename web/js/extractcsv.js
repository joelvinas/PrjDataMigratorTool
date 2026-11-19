"use strict";
/* Extract to CSV: the chosen file is sent to the local server (base64, in memory only), which returns the CSV. The page keeps only counts and the file name; it never shows a
   record value. Nothing here touches the token or the database. */

const MAX_FILE_BYTES = 18 * 1024 * 1024; // the server takes 25 MB of request, and base64 adds a third

let csvUrl = null;

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function resetResult() {
  if (csvUrl) { URL.revokeObjectURL(csvUrl); csvUrl = null; }
  $("resultBox").hidden = true;
  clear($("warnList"));
}

async function convert() {
  resetResult();
  const file = $("fileIn").files[0];
  if (!file) { setMsg("pageMessage", "Choose a .json extract file first.", "err"); return; }
  if (file.size > MAX_FILE_BYTES) { setMsg("pageMessage", "That file is " + fmtBytes(file.size) + ". The limit is about 18 MB.", "err"); return; }
  $("btnConvert").disabled = true;
  setMsg("pageMessage", "Converting...");
  try {
    let res;
    try {
      res = await fetch("/api/extract-csv", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ json_base64: toBase64(await file.arrayBuffer()) }) });
    } catch (e) {
      throw new Error("Could not reach the local server. Is python server.py still running?");
    }
    if (!res.ok) {
      let msg = "Request failed (" + res.status + ")";
      try { msg = (await res.json()).error || msg; } catch (e) { /* non-JSON error */ }
      throw new Error(msg);
    }
    const name = ((res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/) || [])[1] || "extract.csv";
    const blob = await res.blob();
    csvUrl = URL.createObjectURL(blob);
    const link = $("dlLink");
    link.href = csvUrl;
    link.setAttribute("download", name);
    $("resultLine").textContent = name + ": " + fmt(res.headers.get("X-Record-Count")) + " records, " + fmt(res.headers.get("X-Field-Count")) + " fields (" + fmtBytes(blob.size) + ").";
    let warns = [];
    try { warns = JSON.parse(res.headers.get("X-Warnings") || "[]"); } catch (e) { /* no warnings */ }
    warns.forEach((w) => $("warnList").appendChild(el("li", { text: w })));
    $("resultBox").hidden = false;
    setMsg("pageMessage", "Done. Use the button to save the CSV.", "ok");
    link.focus();
  } catch (e) {
    setMsg("pageMessage", e.message, "err");
  } finally {
    $("btnConvert").disabled = !$("fileIn").files.length;
  }
}

startPage("extractcsv", {}, () => {
  $("fileIn").addEventListener("change", () => { resetResult(); setMsg("pageMessage", ""); $("btnConvert").disabled = !$("fileIn").files.length; });
  $("btnConvert").addEventListener("click", convert);
});
