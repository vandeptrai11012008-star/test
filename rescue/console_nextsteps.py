"""IFF Operator Console -- "Next Steps" build (nextsteps-v3).

Author:  Tomas Chadwick <tchadwick@hks.harvard.edu>

This is an ADD-ON launcher. It runs the exact same protocol and API as the
upstream console (it imports Console and make_server from rescue.console and never
modifies them), but serves a page that has a NEXT STEPS button and a visual
"from laptop to field" guide layered in.

    python3 -m rescue.console_nextsteps         # opens http://127.0.0.1:8011

How it stays separate from upstream:
  - rescue/console.html is read, never written.
  - The add-on lives entirely in four sibling files:
        nextsteps.css           styles
        nextsteps.button.html   the header button + BUILD chip
        nextsteps.dialog.html   the guide dialog
        nextsteps.js            open/close behaviour
  - At startup those four are injected into a copy of console.html (held in a
    temp file) at fixed anchors. If an anchor is gone (upstream moved on), we
    fail loudly so the add-on gets updated rather than silently breaking.

Tell this build apart from the plain upstream one by the BUILD: nextsteps-v3
chip in the header.
"""
from __future__ import annotations

import argparse
import atexit
import os
import tempfile
import threading
import webbrowser
from pathlib import Path

from . import console as C   # upstream module -- imported, never modified

BUILD = "nextsteps-v3"
HERE = Path(__file__).resolve().parent

# anchor -> add-on file, and how to weave it in (prefix keeps the anchor in place)
_CSS_ANCHOR = "</style>"
_BTN_ANCHOR = '<button class="hbtn" id="btnDog"'
_END_ANCHOR = "</body>"


def _read(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


def _inject(anchor: str, insert: str, page: str, what: str) -> str:
    """Put `insert` immediately before the first `anchor`. Fail loudly if the
    anchor is missing, so a changed upstream page can't break us silently."""
    if anchor not in page:
        raise RuntimeError(
            "console_nextsteps: anchor for %s not found in console.html "
            "(upstream layout changed). Update the add-on anchors." % what)
    return page.replace(anchor, insert + anchor, 1)


def build_page() -> str:
    """Compose the upstream console.html with the four add-on files woven in."""
    page = C.HTML.read_bytes().decode("utf-8")        # upstream page, untouched on disk
    css = "\n/* ---- nextsteps-v3 add-on ---- */\n" + _read("nextsteps.css")
    btn = _read("nextsteps.button.html")
    dialog = _read("nextsteps.dialog.html")
    js = _read("nextsteps.js")
    page = _inject(_CSS_ANCHOR, css, page, "CSS")
    page = _inject(_BTN_ANCHOR, btn, page, "header button")
    page = _inject(_END_ANCHOR, dialog + "\n<script>\n" + js + "\n</script>\n", page, "dialog + script")
    return page


def _serve_file(page: str) -> Path:
    """Write the composed page to a temp file and point the upstream server at it.
    make_server reads C.HTML on every request, so overriding it here is enough."""
    fd, path = tempfile.mkstemp(prefix="rescue_console_nextsteps_", suffix=".html")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(page)
    p = Path(path)
    atexit.register(lambda: p.unlink(missing_ok=True))
    C.HTML = p                                         # upstream do_GET now serves our page
    return p


def main():
    ap = argparse.ArgumentParser(description="IFF operator console -- Next Steps build (%s)" % BUILD)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()

    _serve_file(build_page())
    console = C.Console()                              # same protocol engine as upstream
    server = C.make_server(console, args.host, args.port)
    url = "http://%s:%d/" % (args.host, server.server_address[1])
    print("IFF operator console [BUILD %s] running at %s (Ctrl-C to stop)" % (BUILD, url))
    print("  add-on by Tomas Chadwick; upstream console.html left unmodified")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
