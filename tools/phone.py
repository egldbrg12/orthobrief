#!/usr/bin/env python3
"""Look at OrthoBrief the way a phone does.

Chrome will not open a window narrower than 500px, so a screenshot taken at 390
is a 500px render with the right-hand side cropped off — which reads as a broken
layout that isn't broken. This loads the page inside an iframe of the width being
tested instead. An iframe is a real viewport: media queries fire, text wraps, and
anything genuinely too wide genuinely overflows.

Start the app first (`python3 app.py`), then:

    python3 tools/phone.py shot.png            a phone-width screenshot
    python3 tools/phone.py shot.png 768        some other width
    python3 tools/phone.py --measure           every element wider than the viewport
    python3 tools/phone.py shot.png --tall     1600px of page instead of 880
    python3 tools/phone.py shot.png --onboard  keep the onboarding dialog up
    python3 tools/phone.py shot.png --css=x.css --js=x.js   inject a variant

`--css` and `--js` are how a design change gets reviewed before it is written:
render the current page with the proposed rules layered on top, look at it, and
only then edit the template. Paths are relative to where you run this.

Standard library only, like the rest of this repo. Needs Chrome installed;
set ORTHOBRIEF_CHROME to point at it if it lives somewhere unusual.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

SRC = os.environ.get("ORTHOBRIEF_SRC", "http://localhost:8087")
PORT = int(os.environ.get("ORTHOBRIEF_PHONE_PORT", "8099"))

CHROME_CANDIDATES = [
    os.environ.get("ORTHOBRIEF_CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
]

args = sys.argv[1:]
flags = {a for a in args if a.startswith("--")}
positional = [a for a in args if not a.startswith("--")]

OUT = os.path.abspath(positional[0] if positional else "shot.png")
WIDTH = int(positional[1]) if len(positional) > 1 else 390
HEIGHT = 1600 if "--tall" in flags else 880
MEASURE = "--measure" in flags


def _flag(name: str, default: str = "") -> str:
    return next((f.split("=", 1)[1] for f in flags if f.startswith(name + "=")), default)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


PATH = _flag("--path", "/")
EXTRA = ""
if _flag("--css"):
    EXTRA += "<style>" + _read(_flag("--css")) + "</style>"
if _flag("--js"):
    # After first paint and after the feed has arrived, so the injected script
    # can act on rendered cards rather than an empty shell.
    EXTRA += ("<script>addEventListener('load',()=>setTimeout(()=>{"
              + _read(_flag("--js")) + "},1200))</script>")

# A profile that has already been through onboarding, so the dialog doesn't sit
# on top of the thing being looked at. `--onboard` is for shooting the dialog.
SEED = "" if "--onboard" in flags else (
    "<script>try{localStorage.setItem('orthobrief.profile',%s);}catch(e){}</script>"
    % json.dumps(json.dumps({"onboarded": True, "role": "resident", "fields": [],
                             "keywords": [], "weights": {}, "made": "2000-01-01"})))

# Reports anything sticking out past the viewport, worst first. Runs inside the
# iframe and hands its answer to the parent, because --dump-dom only dumps the
# top document.
PROBE = """
<script>
addEventListener("load", () => setTimeout(() => {
  const vw = document.documentElement.clientWidth, out = [];
  document.querySelectorAll("*").forEach(el => {
    const r = el.getBoundingClientRect();
    if(r.width > vw + 1 || r.right > vw + 1){
      out.push({t: el.tagName.toLowerCase(), c: String(el.className || "").slice(0, 44),
                id: el.id || "", w: Math.round(r.width), right: Math.round(r.right)});
    }
  });
  const seen = new Set(), keep = [];
  out.sort((a, b) => b.right - a.right).forEach(o => {
    const k = o.t + o.c + o.id;
    if(!seen.has(k)){ seen.add(k); keep.push(o); }
  });
  const payload = JSON.stringify({vw, docw: document.documentElement.scrollWidth,
                                  over: keep.slice(0, 24)});
  const top = (window.parent !== window ? window.parent : window);
  try{ top.document.body.setAttribute("data-probe", payload); }catch(e){}
}, 2000));
</script>
""" if MEASURE else ""

HARNESS = f"""<!doctype html><meta charset="utf-8"><title>phone</title>
<style>html,body{{margin:0;background:#3a3a3a}}
iframe{{width:{WIDTH}px;height:{HEIGHT}px;border:0;display:block;background:#fff}}</style>
<iframe src="{PATH}"></iframe>"""


class Handler(http.server.BaseHTTPRequestHandler):
    """Proxies the running app so the iframe is same-origin and injectable."""

    def log_message(self, *fmt):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/__phone":
            return self._reply(HARNESS.encode(), "text/html; charset=utf-8")
        try:
            body = urllib.request.urlopen(SRC + self.path, timeout=60).read()
        except urllib.error.HTTPError as exc:
            # The app's own 404 (a favicon, a feed that doesn't exist) is not a
            # proxy failure — pass it through quietly rather than blowing up.
            self.send_error(exc.code, "upstream said %d" % exc.code)
            return
        except Exception as exc:  # noqa: BLE001
            # Status lines are latin-1: keep this ASCII or the error handler
            # raises its own error and buries the real one.
            self.send_error(502, "cannot reach %s (is app.py running?)" % SRC)
            print(f"  proxy error: {exc}", file=sys.stderr)
            return
        if self.path.startswith("/api"):
            return self._reply(body, "application/json")
        if self.path.endswith(".xml") or self.path.endswith(".xsl"):
            return self._reply(body, "application/xml; charset=utf-8")
        body = body.replace(b"<head>", b"<head>" + SEED.encode(), 1)
        body = body.replace(b"</head>", (EXTRA + PROBE).encode() + b"</head>", 1)
        self._reply(body, "text/html; charset=utf-8")

    def _reply(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    sys.exit("No Chrome found. Set ORTHOBRIEF_CHROME to its path.")


def run_chrome(out: str, dump_dom: bool = False) -> None:
    """Render once and wait for the file, not for the process.

    Chrome writes its screenshot and then lingers — on macOS it hands off to the
    updater and never exits — so waiting on the process hangs forever. Wait for
    the output to stop growing, then kill the child by pid. Killing by name
    would take down every other headless Chrome on the machine.
    """
    if os.path.exists(out):
        os.remove(out)
    profile = tempfile.mkdtemp(prefix="orthobrief-phone-")
    cmd = [find_chrome(), "--headless", "--disable-gpu", "--hide-scrollbars",
           "--virtual-time-budget=9000",
           f"--window-size={max(WIDTH + 20, 520)},{HEIGHT + 20}",
           f"--user-data-dir={profile}"]
    sink = open(out, "wb") if dump_dom else subprocess.DEVNULL
    cmd.append("--dump-dom" if dump_dom else f"--screenshot={out}")
    cmd.append(f"http://localhost:{PORT}/__phone")
    proc = subprocess.Popen(cmd, stdout=sink, stderr=subprocess.DEVNULL)
    try:
        last, settled = -1, 0
        for _ in range(75):
            time.sleep(1)
            size = os.path.getsize(out) if os.path.exists(out) else 0
            settled = settled + 1 if size == last and size > 0 else 0
            last = size
            if settled >= 2:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if dump_dom:
            sink.close()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> None:
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if MEASURE:
            dump = os.path.join(tempfile.gettempdir(), "orthobrief-phone-dump.html")
            run_chrome(dump, dump_dom=True)
            with open(dump, encoding="utf-8", errors="replace") as fh:
                html = fh.read()
            found = re.search(r'data-probe="([^"]*)"', html)
            if not found:
                sys.exit("No probe result — the page may not have loaded.")
            data = json.loads(found.group(1).replace("&quot;", '"').replace("&amp;", "&"))
            over = data["docw"] - data["vw"]
            print(f"viewport {data['vw']}px, document {data['docw']}px  ->  "
                  + (f"OVERFLOW {over}px" if over > 0 else "fits"))
            for item in data["over"]:
                sel = (item["t"]
                       + ("." + item["c"].replace(" ", ".") if item["c"] else "")
                       + ("#" + item["id"] if item["id"] else ""))
                print(f"  right={item['right']:>5}  width={item['w']:>5}   {sel}")
        else:
            run_chrome(OUT)
            if not os.path.exists(OUT):
                sys.exit("Chrome produced no screenshot.")
            print(f"{OUT}  ({os.path.getsize(OUT) / 1024:.0f} KB, {WIDTH}px wide)")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
