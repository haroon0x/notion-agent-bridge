#!/usr/bin/env python3
"""Local bridge: Chrome extension -> localhost -> `opencode run` -> structured edits.

Stdlib only (no pip install needed).

Endpoints:
  GET  /health            -> {"ok": true, ...}
  POST /run               -> {"command","scope","pageText","selection",...}
                          -> {"ok": true, "edits": [...], "output_text": "..."}

Auth: if NOB_TOKEN is set (recommended), the extension must send it as
`x-bridge-token` header or `token` JSON field. Binds 127.0.0.1 only.

Env:
  NOB_PORT          default 8787
  NOB_TOKEN         default "" (no auth; set it)
  NOB_MODEL         default "" (opencode default model)
  NOB_OPENCODE_BIN  default "opencode"
  NOB_TIMEOUT       default 180 (seconds)
  NOB_MOCK          default "0" (set "1" to skip opencode for offline tests)
"""

import json
import logging
import os
import re
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = logging.getLogger("nob")

PORT = int(os.environ.get("NOB_PORT", "8787"))
EXPECTED_TOKEN = os.environ.get("NOB_TOKEN", "")
DEFAULT_MODEL = os.environ.get("NOB_MODEL", "")
OPENCODE_BIN = os.environ.get("NOB_OPENCODE_BIN", "opencode")
TIMEOUT = int(os.environ.get("NOB_TIMEOUT", "180"))
MOCK = os.environ.get("NOB_MOCK", "0") == "1"

ALLOWED_ORIGINS = {
    "https://www.notion.so",
    "https://notion.so",
    "http://localhost",
}
ALLOWED_MODES = {"replace-selection", "replace-block", "insert-after", "append"}
MAX_PAGE_CHARS = 10000
MAX_EDIT_TEXT = 6000
MAX_EDITS = 10


def build_prompt(command, scope, page_text, selection, url):
    """Build the LLM prompt that forces strict JSON edits output."""
    page_text = (page_text or "")[:MAX_PAGE_CHARS]
    selection = (selection or "")[:4000]
    use_selection = bool(selection) and scope == "selection"
    target = "the SELECTED TEXT" if use_selection else "the PAGE"
    return f"""You edit a Notion page. Rewrite {target} per the user instruction.

USER INSTRUCTION:
{command}

PAGE URL: {url or "(unknown)"}

{"SELECTED TEXT:\n" + selection if use_selection else "(no selection; use full page)"}

FULL PAGE TEXT (may be truncated):
{page_text}

RULES:
- Preserve names, numbers, links. Do not invent facts.
- Keep formatting plain (Notion DOM applies it). No HTML.
- Output ONLY strict JSON, no markdown fences, no commentary:
{{"edits": [{{"mode": "replace-selection|replace-block|insert-after|append", "text": "..."}}]}}
- Modes: "replace-selection" rewrites the selection (preferred when selection exists).
  "append" adds at end. "insert-after" inserts after a point. Max {MAX_EDITS} edits.
- Keep each edit under ~1500 chars when possible; split long output into several edits.
"""


def _strip_fences(s):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s or "", re.DOTALL)
    return m.group(1) if m else (s or "")


def extract_edits(output_text, has_selection):
    """Parse model output into validated edits list. Falls back to one edit."""
    raw = _strip_fences(output_text or "").strip()
    edits = None
    if raw:
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            obj = json.loads(raw[start:end])
            cand = obj.get("edits") if isinstance(obj, dict) else None
            if isinstance(cand, list):
                edits = []
                for e in cand[:MAX_EDITS]:
                    if not isinstance(e, dict):
                        continue
                    mode = str(e.get("mode", "")).strip()
                    text = str(e.get("text", ""))
                    if mode not in ALLOWED_MODES:
                        mode = "replace-selection" if has_selection else "append"
                    text = text.strip()[:MAX_EDIT_TEXT]
                    if not text:
                        continue
                    item = {"mode": mode, "text": text}
                    if e.get("blockId"):
                        item["blockId"] = str(e["blockId"])[:64]
                    edits.append(item)
                return edits
        except (ValueError, json.JSONDecodeError) as exc:
            LOG.debug("edit JSON parse failed: %s", exc)
            edits = None
    if not edits:
        fallback_text = (output_text or "").strip()[:4000]
        if not fallback_text:
            return []
        edits = [{
            "mode": "replace-selection" if has_selection else "append",
            "text": fallback_text,
        }]
    return edits


def _text_from_run_jsonl(stdout):
    """Extract assistant text from `opencode run --format json` stdout.

    Recorded shape (opencode v2.0.26, `opencode run --format json`):
      {"type":"step_start","timestamp":...,"sessionID":"...","part":{...}}
      {"type":"text","timestamp":...,"sessionID":"...","part":{"type":"text","text":"...","time":{...}}}
    One JSON object per line. Longer answers stream as several type=="text"
    events, so concatenate part.text of those events in order. Returns ""
    when no text events are present (caller falls back to legacy parsing).
    """
    chunks = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "text":
            continue
        part = ev.get("part")
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return "".join(chunks)


def _pick_text_from_opencode_json(obj):
    """`opencode run --format json` shape may vary; hunt for the text payload."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for key in ("output", "text", "content", "message", "result", "response"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
        # Some shapes nest: {"session": ..., "message": {...}} or lists
        for val in obj.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                got = _pick_text_from_opencode_json(val[0])
                if got and got is not obj:
                    return got if isinstance(got, str) else json.dumps(got)
        return json.dumps(obj)[:8000]
    return json.dumps(obj)[:8000]


def run_opencode(prompt, model):
    """Run opencode headless and return raw text output."""
    if MOCK:
        return json.dumps({"edits": [{"mode": "append", "text": "[MOCK] " + prompt[:120]}]})
    if model:
        argv = [OPENCODE_BIN, "run", "--format", "json", "-m", model, prompt]
    else:
        argv = [OPENCODE_BIN, "run", "--format", "json", prompt]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError(f"opencode binary not found: {OPENCODE_BIN}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"opencode timed out after {TIMEOUT}s")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"opencode exit {proc.returncode}: {err}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("opencode returned empty output")
    text = _text_from_run_jsonl(out)
    if text.strip():
        return text
    try:
        return _pick_text_from_opencode_json(json.loads(out))
    except json.JSONDecodeError:
        return out


class Handler(BaseHTTPRequestHandler):
    server_version = "NOBridge/0.1"

    def _cors_origin(self):
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") or origin in ALLOWED_ORIGINS:
            return origin
        return "*"

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self._cors_origin())
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self._cors_origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-bridge-token")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"ok": True, "mock": MOCK, "model_default": DEFAULT_MODEL or None})
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/run":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 256 * 1024:
            self._send_json(413, {"ok": False, "error": "payload too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid JSON"})
            return

        token = self.headers.get("x-bridge-token") or payload.get("token") or ""
        if EXPECTED_TOKEN and token != EXPECTED_TOKEN:
            self._send_json(401, {"ok": False, "error": "bad token"})
            return

        command = str(payload.get("command", "")).strip()
        if not command:
            self._send_json(400, {"ok": False, "error": "command is required"})
            return
        scope = payload.get("scope", "selection")
        page_text = str(payload.get("pageText", ""))
        selection = str(payload.get("selection", ""))
        url = str(payload.get("url", ""))
        model = str(payload.get("model", "") or DEFAULT_MODEL or "")

        prompt = build_prompt(command, scope, page_text, selection, url)
        try:
            output_text = run_opencode(prompt, model)
        except RuntimeError as exc:
            LOG.warning("opencode failed: %s", exc)
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        edits = extract_edits(output_text, bool(selection))
        self._send_json(200, {"ok": True, "edits": edits, "output_text": output_text[:8000]})

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not EXPECTED_TOKEN:
        LOG.warning("NOB_TOKEN not set: bridge accepts any localhost caller. Set it for safety.")
    srv = HTTPServer(("127.0.0.1", PORT), Handler)
    LOG.info("bridge on http://127.0.0.1:%d mock=%s model_default=%s", PORT, MOCK, DEFAULT_MODEL or "-")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
