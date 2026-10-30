#!/usr/bin/env python3
"""Prova di avvio del pacchetto Windows su un runner pulito: il browser parte, esegue JavaScript,
carica una pagina dalla rete e la disegna? Non sostituisce una prova a mano (finestre, menu),
ma ferma subito un pacchetto che non si apre, prima che arrivi in una release.

Due giri:
1. dalla cartella estratta così com'è, come chi scompatta lo zip (solo informativo);
2. dopo Imposta-permessi.cmd del pacchetto, che dà alla cartella i permessi di Program Files per
   i processi in AppContainer, come fa l'installer di Chrome: il servizio di rete gira in un
   AppContainer ristretto che deve leggere chrome.exe. Questo giro deve passare tutto.

uso: smoke_windows.py <cartella con il pacchetto estratto> <cartella per i risultati>
Nei risultati: pagina.png (la pagina di prova disegnata) e i log del browser.
"""

import functools
import http.server
import json
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 180
# JavaScript e DOM: se il risultato arriva nella pagina, il renderer funziona
PAGE = """<!doctype html><meta charset="utf-8"><title>prova</title>
<body style="font: 40px sans-serif; margin: 60px"><p id="r">attesa</p>
<script>document.getElementById("r").textContent = "ok " + (6 * 7);</script>
"""
EXPECTED = "ok 42"
SANDBOX_ERROR = "Sandbox cannot access executable"
NETWORK_CRASH = "Network service crashed"
APP_PACKAGES = ["*S-1-15-2-1:(OI)(CI)(RX)", "*S-1-15-2-2:(OI)(CI)(RX)"]


class Browser:
    def __init__(self, exe, work, logs):
        self.exe, self.work, self.logs = exe, work, logs

    def run(self, name, *args):
        cmd = [str(self.exe), "--headless=new", "--no-first-run", "--no-default-browser-check",
               f"--user-data-dir={self.work / ('profilo-' + name)}", "--enable-logging=stderr",
               "--v=0", *args]
        print("+ " + " ".join(cmd), flush=True)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                                 timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            sys.exit(f"{name}: il browser non ha finito in {TIMEOUT} secondi")
        (self.logs / f"{name}.log").write_text(f"codice {res.returncode}\n\n{res.stderr}",
                                               encoding="utf-8")
        return res


def serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def round_(browser, tag, page, url, shot):
    """Un giro di prove; restituisce l'elenco dei problemi."""
    problems = []
    res = browser.run(f"{tag}-file", "--dump-dom", page.as_uri())
    if EXPECTED not in res.stdout:
        problems.append(f"pagina da file: manca \"{EXPECTED}\" (codice {res.returncode})")
    res = browser.run(f"{tag}-rete", "--dump-dom", f"{url}/prova.html")
    if EXPECTED not in res.stdout:
        problems.append(f"pagina dalla rete (servizio di rete): manca \"{EXPECTED}\" "
                        f"(codice {res.returncode})")
    for marker in (SANDBOX_ERROR, NETWORK_CRASH):
        if marker in res.stderr:
            problems.append(f"nel log: \"{marker}\"")
    if shot:
        res = browser.run(f"{tag}-screenshot", f"--screenshot={shot}", "--window-size=1280,800",
                          f"{url}/prova.html")
        if not (shot.exists() and shot.stat().st_size > 1000):
            problems.append(f"nessuno screenshot della pagina di prova (codice {res.returncode})")
    # solo informativo: chrome://sandbox dice se il servizio di rete è davvero isolato
    res = browser.run(f"{tag}-sandbox", "--allow-chrome-scheme-url", "--virtual-time-budget=8000",
                      "--dump-dom", "chrome://sandbox")
    (browser.logs / f"{tag}-sandbox.html").write_text(res.stdout, encoding="utf-8")
    row = re.search(r"<tr>(?:(?!</tr>).)*Network Service(?:(?!</tr>).)*</tr>", res.stdout, re.S)
    print(f"sandbox del servizio di rete ({tag}): "
          + (re.sub(r"<[^>]+>", " | ", row.group(0))[:300] if row else "(riga non trovata)"), flush=True)
    print(f"giro \"{tag}\": " + ("tutto bene" if not problems else "; ".join(problems)), flush=True)
    return problems


def main():
    pkg, out = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    exe = next(pkg.rglob("chrome.exe"), None)
    if exe is None:
        sys.exit("chrome.exe non c'è nel pacchetto")
    exe = exe.resolve()
    version = json.loads((ROOT / "upstream.lock").read_text())["chromium_version"]
    if not (exe.parent / f"{version}.manifest").exists():
        sys.exit(f"il pacchetto non è della versione {version} (manca {version}.manifest)")
    print(f"pacchetto: {exe.parent} (Chromium {version})")

    work = Path(tempfile.mkdtemp())
    page = work / "prova.html"
    page.write_text(PAGE, encoding="utf-8")
    url = serve(work)
    browser = Browser(exe, work, out)

    plain = round_(browser, "estratto", page, url, None)
    script = exe.parent / "Imposta-permessi.cmd"
    if script.exists():
        subprocess.run(["cmd", "/c", str(script)], check=True, stdin=subprocess.DEVNULL)
    else:  # pacchetti di prima che lo script esistesse
        subprocess.run(["icacls", str(exe.parent), "/grant", *APP_PACKAGES, "/T", "/Q"], check=True)
    failed = round_(browser, "permessi", page, url, out / "pagina.png")

    # solo informativo: nome e versione che il browser mostra di sé
    res = browser.run("version", "--allow-chrome-scheme-url", "--dump-dom", "chrome://version")
    lines = [line.strip() for line in res.stdout.splitlines() if version in line]
    print("chrome://version: " + (lines[0][:300] if lines else "(non letto)"))

    print("\nzip estratto così com'è: " + ("funziona" if not plain else "; ".join(plain)))
    if failed:
        sys.exit("prova di avvio fallita:\n- " + "\n- ".join(failed))
    print("prova di avvio superata")


if __name__ == "__main__":
    main()
