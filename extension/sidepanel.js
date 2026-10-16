// sidepanel.js - no build step, plain script.
const $ = (id) => document.getElementById(id);
let pendingEdits = [];

function log(msg) {
  const el = $("log");
  el.textContent = `${new Date().toLocaleTimeString()}  ${msg}\n` + el.textContent;
}

async function loadSettings() {
  const s = await chrome.storage.local.get(["serverUrl", "bridgeToken", "model"]);
  if (s.serverUrl) $("serverUrl").value = s.serverUrl;
  if (s.bridgeToken) $("bridgeToken").value = s.bridgeToken;
  if (s.model) $("model").value = s.model;
}

async function saveSettings() {
  await chrome.storage.local.set({
    serverUrl: $("serverUrl").value.trim().replace(/\/$/, ""),
    bridgeToken: $("bridgeToken").value,
    model: $("model").value.trim()
  });
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function ensureContent(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "NAB_PING" });
    return true;
  } catch (_e) {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    await new Promise((r) => setTimeout(r, 300));
    await chrome.tabs.sendMessage(tabId, { type: "NAB_PING" });
    return true;
  }
}

async function getContext(tabId) {
  return chrome.tabs.sendMessage(tabId, { type: "NAB_GET_CONTEXT" });
}

function renderPreview(edits, outputText) {
  pendingEdits = edits;
  $("apply").disabled = edits.length === 0;
  const box = $("preview");
  box.innerHTML = "";
  if (!edits.length) {
    box.textContent = outputText
      ? `Model returned no structured edits. Raw output:\n\n${outputText.slice(0, 2000)}`
      : "No edits yet.";
    return;
  }
  for (const e of edits) {
    const div = document.createElement("div");
    div.className = "edit";
    const m = document.createElement("div");
    m.className = "mode";
    m.textContent = e.mode + (e.blockId ? `  (${String(e.blockId).slice(0, 8)})` : "");
    const t = document.createElement("div");
    t.textContent = String(e.text || "").slice(0, 1200);
    div.appendChild(m);
    div.appendChild(t);
    box.appendChild(div);
  }
}

$("run").addEventListener("click", async () => {
  await saveSettings();
  const command = $("command").value.trim();
  if (!command) { log("Type an instruction first."); return; }
  const serverUrl = $("serverUrl").value.trim().replace(/\/$/, "");
  const token = $("bridgeToken").value;
  const model = $("model").value.trim();
  const scope = $("scope").value;
  $("run").disabled = true;
  try {
    const tab = await activeTab();
    if (!tab || !/^https:\/\/(www\.)?notion\.so\//.test(tab.url || "")) {
      log("Open a Notion page first, then run.");
      return;
    }
    await ensureContent(tab.id);
    const ctx = await getContext(tab.id);
    if (!ctx || !ctx.ok) throw new Error(`context failed: ${ctx?.error || "unknown"}`);
    log(`Context: ${ctx.blockCount} blocks, sel=${(ctx.selection || "").length} chars. Asking bridge…`);
    const res = await fetch(`${serverUrl}/run`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "x-bridge-token": token } : {})
      },
      body: JSON.stringify({
        command,
        scope,
        pageText: ctx.fullText,
        selection: ctx.selection,
        selectionBlockId: ctx.selectionBlockId,
        url: ctx.url,
        model: model || undefined
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `bridge HTTP ${res.status}`);
    renderPreview(data.edits || [], data.output_text || "");
    log(`Bridge returned ${(data.edits || []).length} edit(s). Review, then Apply.`);
  } catch (err) {
    log(`ERROR: ${err.message}`);
  } finally {
    $("run").disabled = false;
  }
});

$("apply").addEventListener("click", async () => {
  if (!pendingEdits.length) return;
  $("apply").disabled = true;
  try {
    const tab = await activeTab();
    await ensureContent(tab.id);
    const r = await chrome.tabs.sendMessage(tab.id, {
      type: "NAB_APPLY_EDITS",
      edits: pendingEdits
    });
    const okCount = (r.results || []).filter((x) => x.ok).length;
    log(`Applied ${okCount}/${pendingEdits.length}. Undo with Ctrl+Z if needed.`);
  } catch (err) {
    log(`Apply ERROR: ${err.message}`);
  } finally {
    $("apply").disabled = pendingEdits.length === 0;
  }
});

["serverUrl", "bridgeToken", "model"].forEach((id) => {
  $(id).addEventListener("change", saveSettings);
});
loadSettings();
