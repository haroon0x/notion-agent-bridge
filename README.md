# Notion Agent Bridge

Chrome sidepanel -> localhost bridge -> any agent CLI (`opencode`, `claude`, `codex`...) -> edits applied to the Notion page DOM.

## Quick start

```bash
./start.sh    # starts bridge, prints the token (stable, paste once into sidepanel)
./stop.sh     # stops it
```

```
Notion tab (content.js) <-> sidepanel <-> http://127.0.0.1:8787/run <-> agent CLI

Backend-agnostic: the bridge shells out to one CLI binary (`NOB_OPENCODE_BIN`,
default `opencode`). Point it at another `run`-compatible CLI to switch providers.
```

No dependencies. Server is Python stdlib only. Extension is vanilla JS (MV3).

## Layout

```
extension/
  manifest.json
  background.js
  content.js      # context extraction + execCommand edits (no innerHTML)
  sidepanel.html
  sidepanel.js
server/
  bridge.py
  test_bridge.py
```

## 1. Prerequisites

- `opencode` authenticated (`opencode auth` or login once; `opencode run "hi"` works).
- Chrome 114+.
- Python 3.10+.

## 2. Run the bridge

```bash
export NOB_TOKEN="$(openssl rand -hex 16)"   # required for safety
echo "token: $NOB_TOKEN"
export NOB_PORT=8787
# optional: export NOB_MODEL="anthropic/claude-sonnet-4-5"
python3 server/bridge.py
```

Health check:

```bash
curl http://127.0.0.1:8787/health
```

Mock mode (no LLM, offline test):

```bash
NOB_MOCK=1 NOB_TOKEN=test python3 server/bridge.py
```

## 3. Load the extension

1. `chrome://extensions` -> Developer mode -> Load unpacked -> `extension/`.
2. Open a Notion page, click the extension icon to open the sidepanel.
3. Paste bridge URL + token (+ optional model override).

## 4. Use

1. Select text in Notion (or leave empty for whole page).
2. Type instruction, Generate, review preview, Apply.
3. Undo with Ctrl+Z if an edit looks wrong.

## 5. Verify

```bash
python3 -m py_compile server/bridge.py
python3 -m unittest discover -s server -v
node --check extension/content.js && node --check extension/sidepanel.js && node --check extension/background.js
python3 -c "import json;json.load(open('extension/manifest.json'));print('manifest ok')"
```

E2E with mock server:

```bash
NOB_MOCK=1 NOB_TOKEN=test python3 server/bridge.py &
curl -s http://127.0.0.1:8787/run -H 'Content-Type: application/json' -H 'x-bridge-token: test' \
  -d '{"command":"make bullets","scope":"page","pageText":"hello","selection":"","url":"https://notion.so/x"}'
```

Browser DOM test (real Chromium, runs `content.js` verbatim on Windows):

```bat
pip install playwright
playwright install chromium
python extension\test\test_content_dom.py
```

## Security notes

- Binds `127.0.0.1` only. Set `NOB_TOKEN`; without it any localhost process can call it.
- Extension host permissions are scoped to `notion.so` + `127.0.0.1`.
- Page text sent to the bridge is truncated (~10k chars) and passed to your model provider via opencode.

## Troubleshooting

- `401 bad token`: token in sidepanel must equal `NOB_TOKEN`.
- Sidepanel says content failed on SPA nav: reload the Notion tab once (content script re-injects automatically on click too).
- `opencode exit N`: run `opencode run "hi"` in terminal to fix auth/model first.
- Edits land in wrong block: select text first; `replace-selection` uses the selection anchor block.
