#!/usr/bin/env python3
"""Prova di avvio del pacchetto Windows su un runner pulito: il browser parte, esegue JavaScript
e disegna una pagina? Non sostituisce una prova a mano (finestre, menu, rete), ma ferma subito
un pacchetto che non si apre, prima che arrivi in una release.

uso: smoke_windows.py <cartella con il pacchetto estratto> <cartella per i risultati>
Nei risultati: pagina.png (la pagina di prova disegnata) e i log del browser.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 180
# JavaScript, WebAssembly e DOM: se il risultato arriva nella pagina, il renderer funziona
PAGE = """<!doctype html><meta charset="utf-8"><title>prova</title>
<body style="font: 40px sans-serif; margin: 60px"><p id="r">attesa</p>
<script>document.getElementById("r").textContent = "ok " + (6 * 7) + " " + typeof WebAssembly;</script>
"""
EXPECTED = "ok 42 object"


def chrome(exe, profile, logs, name, *args):
    cmd = [str(exe), "--headless=new", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={profile}", "--enable-logging=stderr", "--v=0", *args]
    print("+ " + " ".join(cmd), flush=True)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        sys.exit(f"{name}: il browser non ha finito in {TIMEOUT} secondi")
    (logs / f"{name}.log").write_text(f"codice {res.returncode}\n\n{res.stderr}", encoding="utf-8")
    return res


def main():
    pkg, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    exe = next(pkg.rglob("chrome.exe"), None)
    if exe is None:
        sys.exit("chrome.exe non c'è nel pacchetto")
    version = json.loads((ROOT / "upstream.lock").read_text())["chromium_version"]
    if not (exe.parent / f"{version}.manifest").exists():
        sys.exit(f"il pacchetto non è della versione {version} (manca {version}.manifest)")
    print(f"pacchetto: {exe.parent} (Chromium {version})")

    work = Path(tempfile.mkdtemp())
    page = work / "prova.html"
    page.write_text(PAGE, encoding="utf-8")
    profile = work / "profilo"
    failed = []

    res = chrome(exe, profile, out, "dom", "--dump-dom", page.as_uri())
    if EXPECTED in res.stdout:
        print(f"pagina eseguita: \"{EXPECTED}\"")
    else:
        failed.append(f"la pagina di prova non mostra \"{EXPECTED}\" (codice {res.returncode})")
        print(res.stdout[-2000:])
        print(res.stderr[-4000:])

    shot = out / "pagina.png"
    res = chrome(exe, profile, out, "screenshot", f"--screenshot={shot}", "--window-size=1280,800",
                 page.as_uri())
    if shot.exists() and shot.stat().st_size > 1000:
        print(f"pagina disegnata: {shot.name}, {shot.stat().st_size} byte")
    else:
        failed.append(f"nessuno screenshot della pagina di prova (codice {res.returncode})")
        print(res.stderr[-4000:])

    # solo informativo: nome e versione che il browser mostra di sé
    res = chrome(exe, profile, out, "version", "--dump-dom", "chrome://version")
    lines = [line.strip() for line in res.stdout.splitlines() if version in line]
    print("chrome://version: " + (lines[0][:300] if lines else "(non letto)"))

    if failed:
        sys.exit("prova di avvio fallita:\n- " + "\n- ".join(failed))
    print("prova di avvio superata")


if __name__ == "__main__":
    main()
