"""Real-DOM test for extension/content.js in genuine Blink (Chromium).

Runs on Windows (or anywhere with Playwright browsers):
    pip install playwright
    playwright install chromium
    python test_content_dom.py

What it executes for real: the UNMODIFIED extension/content.js drives a
Notion-shaped fixture (fixture-notion.html) - real layout, real Selection,
real focus, real document.execCommand insertText/insertParagraph, real
InputEvent dispatch. Only the 2-line message transport is shimmed
(window.chrome stub captures the onMessage listener); every line of
content.js under test runs verbatim.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE_URI = (HERE / "fixture-notion.html").as_uri()
CONTENT_JS = HERE.parent / "content.js"

SHIM_JS = """() => {
  window.__nabHandlers = window.__nabHandlers || [];
  window.chrome = { runtime: { onMessage: {
    addListener: (fn) => window.__nabHandlers.push(fn)
  } } };
}"""

CALL_JS = """async (msg) => {
  const h = window.__nabHandlers[window.__nabHandlers.length - 1];
  return await new Promise((resolve) => h(msg, {}, resolve));
}"""

SELECT_BETA_JS = """() => {
  const ed = document.querySelector('[data-block-id="b2"] [contenteditable="true"]');
  const node = ed.firstChild;
  const range = document.createRange();
  range.setStart(node, 13);
  range.setEnd(node, 17); // selects "beta" in "Second block beta"
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  return sel.toString();
}"""

BLOCK_TEXT_JS = """(id) => {
  const b = document.querySelector(`[data-block-id="${id}"]`);
  return b ? b.innerText : null;
}"""


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright module missing: run `pip install playwright` first.")
        return 2
    if not CONTENT_JS.exists():
        print(f"content.js not found at {CONTENT_JS}")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_init_script(SHIM_JS)
        page = ctx.new_page()
        page.goto(FIXTURE_URI)
        page.add_script_tag(path=str(CONTENT_JS))
        page.add_script_tag(path=str(CONTENT_JS))  # 2nd inject must be ignored
        n_handlers = page.evaluate("() => window.__nabHandlers.length")
        check("double-inject guard: single listener", n_handlers == 1, f"got {n_handlers}")

        call = lambda msg: page.evaluate(CALL_JS, msg)  # noqa: E731

        ping = call({"type": "NAB_PING"})
        check("NAB_PING", ping.get("ok") is True and ping.get("injected") is True, str(ping))

        sel = page.evaluate(SELECT_BETA_JS)
        check("fixture selection is 'beta'", sel == "beta", repr(sel))

        ctx_data = call({"type": "NAB_GET_CONTEXT"})
        check("context ok", ctx_data.get("ok") is True, str(ctx_data)[:200])
        check("selection text", ctx_data.get("selection") == "beta", repr(ctx_data.get("selection")))
        check("selectionBlockId", ctx_data.get("selectionBlockId") == "b2",
              repr(ctx_data.get("selectionBlockId")))
        ft = ctx_data.get("fullText", "")
        for snippet in ("First block alpha", "Second block beta", "Third block gamma"):
            check(f"fullText contains {snippet!r}", snippet in ft)
        check("overlay menu excluded", "Menu item must be excluded" not in ft)
        check("empty block skipped", ctx_data.get("blockCount") == 4,
              f"blockCount={ctx_data.get('blockCount')}")
        check("fullText truncated to cap", len(ft) <= 12000, f"len={len(ft)}")
        check("block order preserved", ft.index("First block alpha") < ft.index("Second block beta"))

        r = call({"type": "NAB_APPLY_EDITS",
                  "edits": [{"mode": "replace-selection", "text": "BETA-REPLACED"}]})
        res = r.get("results", [{}])[0]
        check("replace-selection applied", res.get("ok") is True, str(res))
        b2 = page.evaluate(BLOCK_TEXT_JS, "b2")
        check("b2 text replaced", b2 == "Second block BETA-REPLACED", repr(b2))
        check("b1 untouched", page.evaluate(BLOCK_TEXT_JS, "b1") == "First block alpha")
        check("b3 untouched", page.evaluate(BLOCK_TEXT_JS, "b3") == "Third block gamma")

        r = call({"type": "NAB_APPLY_EDITS",
                  "edits": [{"mode": "append", "text": "Appended line here"}]})
        check("append applied", r.get("results", [{}])[0].get("ok") is True, str(r))
        # DOM-level check: b4 holds 20000 chars, so the tail sits beyond the
        # 12000-char fullText cap and cannot be asserted via context.
        b4t = page.evaluate(BLOCK_TEXT_JS, "b4") or ""
        check("appended text present in b4 DOM", "Appended line here" in b4t)

        r = call({"type": "NAB_APPLY_EDITS", "edits": [{"mode": "replace-block"}]})
        check("replace-block without id fails cleanly",
              r.get("results", [{}])[0].get("ok") is False, str(r))
        r = call({"type": "NAB_APPLY_EDITS", "edits": [{"mode": "append", "text": "   "}]})
        check("empty text fails cleanly",
              r.get("results", [{}])[0].get("ok") is False, str(r))
        r = call({"type": "NOPE_UNKNOWN"})
        check("unknown message rejected", r.get("ok") is False, str(r))

        browser.close()
    print("ALL DOM TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
