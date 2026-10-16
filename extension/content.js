// content.js - runs in Notion pages. Extracts context, applies edits via
// contenteditable + execCommand so Notion's React state stays in sync.
// Never use innerHTML on Notion blocks.
(function () {
  if (window.__nabInjected) return;
  window.__nabInjected = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const MAX_CHARS = 12000;

  function closestBlockId(node) {
    if (!node) return null;
    const el = node.nodeType === 1 ? node : node.parentElement;
    if (!el || !el.closest) return null;
    const b = el.closest("[data-block-id]");
    return b ? b.getAttribute("data-block-id") : null;
  }

  function collectBlocks() {
    const root =
      document.querySelector(".notion-page-content") ||
      document.querySelector('[placeholder="Untitled"]')?.closest("div") ||
      document.body;
    const nodes = root.querySelectorAll("[data-block-id]");
    const blocks = [];
    let total = 0;
    for (const n of nodes) {
      // Skip UI chrome inside overlay menus
      if (n.closest('[role="menu"], [data-overlay="true"]')) continue;
      const text = (n.innerText || "").replace(/\s+\n/g, "\n").trim();
      if (!text) continue;
      const id = n.getAttribute("data-block-id");
      const room = MAX_CHARS - total;
      if (room <= 0) break;
      blocks.push({ id, text: text.slice(0, room) });
      total += text.length;
      if (total >= MAX_CHARS) break;
    }
    return blocks;
  }

  function findEditable(blockId) {
    if (blockId) {
      const block = document.querySelector(`[data-block-id="${CSS.escape(blockId)}"]`);
      if (block) {
        const ed = block.querySelector('[contenteditable="true"]');
        if (ed) return ed;
        if (block.getAttribute("contenteditable") === "true") return block;
      }
    }
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0) {
      const anchor = sel.anchorNode;
      const host = anchor && anchor.nodeType === 1 ? anchor : anchor?.parentElement;
      const ed = host?.closest?.('[contenteditable="true"]');
      if (ed) return ed;
    }
    return document.querySelector('.notion-page-content [contenteditable="true"]');
  }

  function focusAtEnd(editable) {
    editable.focus();
    const range = document.createRange();
    range.selectNodeContents(editable);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  // Types via execCommand so Notion observes real input events.
  function insertTextRobust(editable, text) {
    focusAtEnd(editable);
    // Chunk to avoid per-call limits on very long strings.
    const CHUNK = 1500;
    for (let i = 0; i < text.length; i += CHUNK) {
      const part = text.slice(i, i + CHUNK);
      const ok = document.execCommand("insertText", false, part);
      if (!ok) {
        // Fallback: InputEvent insertion (works in Chrome contenteditable).
        editable.dispatchEvent(
          new InputEvent("beforeinput", {
            bubbles: true,
            cancelable: true,
            inputType: "insertText",
            data: part
          })
        );
        document.execCommand("insertText", false, part);
      }
    }
    editable.dispatchEvent(new InputEvent("input", { bubbles: true }));
  }

  function selectAllIn(editable) {
    editable.focus();
    const range = document.createRange();
    range.selectNodeContents(editable);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  async function applyOne(edit) {
    const mode = edit.mode || "append";
    const text = String(edit.text ?? "");
    if (!text.trim()) return { ok: false, error: "empty text" };

    if (mode === "replace-selection") {
      const editable = findEditable(edit.blockId || null);
      if (!editable) return { ok: false, error: "no editable for selection" };
      editable.focus();
      await sleep(60);
      // If there is a collapsed caret, select block content so replace is visible.
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) selectAllIn(editable);
      const ok = document.execCommand("insertText", false, text.slice(0, 6000));
      if (!ok) insertTextRobust(editable, text.slice(0, 6000));
      await sleep(60);
      return { ok: true, mode };
    }

    if (mode === "replace-block") {
      if (!edit.blockId) return { ok: false, error: "replace-block needs blockId" };
      const editable = findEditable(edit.blockId);
      if (!editable) return { ok: false, error: "block not found" };
      selectAllIn(editable);
      document.execCommand("insertText", false, text.slice(0, 6000));
      await sleep(60);
      return { ok: true, mode };
    }

    // insert-after / append: focus anchor, open a new paragraph, type there.
    let anchor = null;
    if (mode === "insert-after" && edit.blockId) anchor = findEditable(edit.blockId);
    if (!anchor) {
      const editables = document.querySelectorAll(
        '.notion-page-content [contenteditable="true"]'
      );
      anchor = editables[editables.length - 1] || findEditable(null);
    }
    if (!anchor) return { ok: false, error: "no anchor editable" };
    focusAtEnd(anchor);
    await sleep(60);
    document.execCommand("insertParagraph", false, null);
    await sleep(80);
    const target =
      document.activeElement &&
      document.activeElement.getAttribute?.("contenteditable") === "true"
        ? document.activeElement
        : anchor;
    // Split long inserts across paragraphs so Notion stays responsive.
    const paras = text.split(/\n/).slice(0, 40);
    for (let i = 0; i < paras.length; i++) {
      const line = paras[i].slice(0, 2000);
      if (line) insertTextRobust(target, line);
      if (i < paras.length - 1) {
        document.execCommand("insertParagraph", false, null);
        await sleep(40);
      }
    }
    return { ok: true, mode };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      try {
        if (msg.type === "NAB_PING") {
          sendResponse({ ok: true, injected: true });
        } else if (msg.type === "NAB_GET_CONTEXT") {
          const blocks = collectBlocks();
          const fullText = blocks.map((b) => b.text).join("\n").slice(0, MAX_CHARS);
          const sel = window.getSelection();
          const selection = sel ? String(sel.toString()).slice(0, 4000) : "";
          const selectionBlockId = sel ? closestBlockId(sel.anchorNode) : null;
          sendResponse({
            ok: true,
            url: location.href,
            title: document.title,
            fullText,
            selection,
            selectionBlockId,
            blocks: blocks.slice(0, 120),
            blockCount: blocks.length
          });
        } else if (msg.type === "NAB_APPLY_EDITS") {
          const edits = Array.isArray(msg.edits) ? msg.edits.slice(0, 10) : [];
          const results = [];
          for (const e of edits) {
            try {
              results.push(await applyOne(e));
            } catch (err) {
              results.push({ ok: false, error: String(err && err.message || err) });
            }
            await sleep(120);
          }
          sendResponse({ ok: true, results });
        } else {
          sendResponse({ ok: false, error: "unknown message" });
        }
      } catch (err) {
        sendResponse({ ok: false, error: String(err && err.message || err) });
      }
    })();
    return true;
  });
})();
