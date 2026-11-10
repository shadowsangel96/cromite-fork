#!/usr/bin/env python3
"""Le scritte delle nostre patch: da strings/fork_strings.json ai file di Chromium.

Genera, in overlay/ (copiati così come sono sopra i sorgenti):
  chrome/app/fork_strings.grd                  le scritte in inglese, lingua di partenza
  chrome/app/resources/fork_strings_it.xtb     l'italiano, con gli ID calcolati da grit
  chrome/browser/fork/fork_webui_strings.h     gli elenchi da dare alle pagine del browser
  chrome/browser/resources/ublock/fork-messages.json   le scritte del pannello di uBlock

uso: tools/fork_strings.py [--chromium <src di Chromium>] [--check]
--check non scrive niente: esce con 1 se i file in overlay non corrispondono al JSON.
Serve un albero di Chromium (per grit e per l'elenco delle lingue): --chromium, CHROMIUM_SRC,
oppure quello della cartella di build (FORK_BUILD_DIR, come per gli script di tools/build).
"""
import argparse
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "strings" / "fork_strings.json"
OVERLAY = ROOT / "overlay"
GRD = "chrome/app/fork_strings.grd"
XTB = "chrome/app/resources/fork_strings_it.xtb"
HEADER = "chrome/browser/fork/fork_webui_strings.h"
UBLOCK = "chrome/browser/resources/ublock/fork-messages.json"
PAGES = {
    "proxy": "kForkProxyStrings",
    "site_panel": "kForkSitePanelStrings",
    "profilo": "kForkProfiloStrings",
    "settings": "kForkSettingsStrings",
}
PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def ids_name(key):
    return "IDS_FORK_" + re.sub(r"(?<!^)(?=[A-Z])", "_", key).upper()


def js_name(key):
    return "fork" + key[0].upper() + key[1:]


def placeholders(entry):
    """I segnaposto nell'ordine in cui compaiono in inglese: il n-esimo diventa $n, tranne quelli
    con un contenuto fisso (per esempio i link) dati in "ph"."""
    fixed = entry.get("ph", {})
    order = []
    for name in PLACEHOLDER.findall(entry["en"]):
        if name not in order:
            order.append(name)
    numbered = [n for n in order if n not in fixed]
    content = {n: f"${i + 1}" for i, n in enumerate(numbered)}
    content.update(fixed)
    it_names = set(PLACEHOLDER.findall(entry["it"]))
    if it_names != set(order):
        raise SystemExit(f"segnaposto diversi tra inglese e italiano: {entry['en']!r}")
    return content


def grd_message(key, entry, shared):
    """shared: l'inglese è uguale a quello di un'altra chiave con un italiano diverso. Il
    "meaning" tiene distinte le due traduzioni (senza, grit le fonderebbe in una)."""
    content = placeholders(entry)
    parts = PLACEHOLDER.split(entry["en"])
    text = "".join(
        f'<ph name="{p}">{html.escape(content[p], quote=False)}</ph>' if i % 2
        else html.escape(p, quote=False) for i, p in enumerate(parts))
    if text != text.strip():
        text = "'''" + text + "'''"
    desc = html.escape(entry.get("desc", "Interface of the fork."), quote=True)
    meaning = f' meaning="{key}"' if shared else ""
    return f'      <message name="{ids_name(key)}" desc="{desc}"{meaning}>{text}</message>'


def locale_outputs(src):
    """Le uscite per lingua come nei .grd di Chromium (stesso elenco, stesse eccezioni)."""
    model = (src / "components/privacy_sandbox_strings.grd").read_text()
    block = model[model.index("<outputs>"):model.index("</outputs>")]
    lines = [l for l in block.splitlines()
             if "data_package" in l or l.strip().startswith(("<if", "</if"))]
    return "\n".join(l.replace("privacy_sandbox_strings_", "fork_strings_") for l in lines)


def make_grd(strings, src):
    by_text = {}
    for entry in strings.values():
        by_text.setdefault(entry["en"], set()).add(entry["it"])
    messages = "\n".join(grd_message(k, v, len(by_text[v["en"]]) > 1)
                         for k, v in strings.items())
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- Generato da tools/fork_strings.py da strings/fork_strings.json: non modificare a mano. -->
<grit latest_public_release="0" current_release="1" source_lang_id="en" enc_check="möl">
  <outputs>
    <output filename="grit/fork_strings.h" type="rc_header">
      <emit emit_type='prepend'></emit>
    </output>
{locale_outputs(src)}
  </outputs>
  <translations>
    <file path="resources/fork_strings_it.xtb" lang="it" />
  </translations>
  <release seq="1" allow_pseudo="false">
    <messages fallback_to_english="true">
{messages}
    </messages>
  </release>
</grit>
'''


def message_ids(grd_text, src):
    """Gli ID delle traduzioni come li calcola grit, leggendo il .grd generato."""
    sys.path.insert(0, str(src / "tools" / "grit"))
    from grit import grd_reader
    from grit.node import message as message_node
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "chrome" / "app" / "fork_strings.grd"
        path.parent.mkdir(parents=True)
        path.write_text(grd_text)
        (path.parent / "resources").mkdir()
        (path.parent / "resources" / "fork_strings_it.xtb").write_text(
            '<?xml version="1.0" ?>\n<!DOCTYPE translationbundle>\n<translationbundle lang="it">\n</translationbundle>\n')
        root = grd_reader.Parse(str(path), first_ids_file=None, skip_validation_checks=True)
        ids = {}
        for node in root.Preorder():
            if isinstance(node, message_node.MessageNode):
                ids[node.attrs["name"]] = node.GetCliques()[0].GetId()
        return ids


def make_xtb(strings, ids):
    out = ['<?xml version="1.0" ?>', "<!DOCTYPE translationbundle>", '<translationbundle lang="it">']
    seen = {}
    for key, entry in strings.items():
        parts = PLACEHOLDER.split(entry["it"])
        text = "".join(f'<ph name="{p}"/>' if i % 2 else html.escape(p, quote=False)
                       for i, p in enumerate(parts))
        message_id = ids[ids_name(key)]
        if message_id in seen:
            # Stesso inglese e stesso italiano: per grit è una sola traduzione.
            if seen[message_id] != text:
                raise SystemExit(f"{key}: stesso ID di un'altra scritta con un italiano diverso")
            continue
        seen[message_id] = text
        out.append(f'<translation id="{message_id}">{text}</translation>')
    out.append("</translationbundle>")
    return "\n".join(out) + "\n"


def make_header(strings):
    lines = [
        "// Generato da tools/fork_strings.py da strings/fork_strings.json: non modificare a mano.",
        "#ifndef CHROME_BROWSER_FORK_FORK_WEBUI_STRINGS_H_",
        "#define CHROME_BROWSER_FORK_FORK_WEBUI_STRINGS_H_",
        "",
        '#include "chrome/grit/fork_strings.h"',
        '#include "ui/base/webui/web_ui_util.h"',
        "",
        "namespace browser_fork {",
        "",
    ]
    for page, name in PAGES.items():
        keys = [k for k, v in strings.items() if page in v["pages"]]
        lines.append(f"// The strings of the page \"{page}\", for loadTimeData and $i18n{{}}.")
        lines.append(f"inline constexpr webui::LocalizedString {name}[] = {{")
        lines += [f'    {{"{js_name(k)}", {ids_name(k)}}},' for k in keys]
        lines += ["};", ""]
    lines += ["}  // namespace browser_fork", "", "#endif  // CHROME_BROWSER_FORK_FORK_WEBUI_STRINGS_H_", ""]
    return "\n".join(lines)


def make_ublock(strings):
    """Per chrome.i18n del pannello di uBlock: {"fork_<chiave>": {"en": ..., "it": ...}}; i segnaposto
    diventano $1, $2... come vuole il formato dei messaggi delle estensioni."""
    out = {}
    for key, entry in strings.items():
        if "ublock" not in entry["pages"]:
            continue
        content = placeholders(entry)
        out["fork_" + key] = {lang: PLACEHOLDER.sub(lambda m: content[m.group(1)], entry[lang])
                              for lang in ("en", "it")}
    return json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def default_chromium():
    if os.environ.get("CHROMIUM_SRC"):
        return os.environ["CHROMIUM_SRC"]
    build = os.environ.get("FORK_BUILD_DIR")
    found = sorted(Path(build).glob("chromium-*/src")) if build else []
    return str(found[-1]) if found else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chromium", default=default_chromium())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.chromium:
        raise SystemExit("serve un albero di Chromium: --chromium, CHROMIUM_SRC o FORK_BUILD_DIR")
    src = Path(args.chromium)
    strings = json.loads(SOURCE.read_text())["strings"]
    for key, entry in strings.items():
        if not re.fullmatch(r"[a-z][A-Za-z0-9]*", key):
            raise SystemExit(f"chiave non valida: {key}")
        unknown = set(entry["pages"]) - set(PAGES) - {"native", "ublock"}
        if unknown:
            raise SystemExit(f"{key}: pagine sconosciute {unknown}")
    grd = make_grd(strings, src)
    files = {
        GRD: grd,
        XTB: make_xtb(strings, message_ids(grd, src)),
        HEADER: make_header(strings),
        UBLOCK: make_ublock(strings),
    }
    stale = []
    for rel, text in files.items():
        path = OVERLAY / rel
        if path.is_file() and path.read_text() == text:
            continue
        stale.append(rel)
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if args.check:
        if stale:
            print("Da rigenerare con tools/fork_strings.py:", ", ".join(stale))
            return 1
        print(f"Scritte in ordine: {len(strings)}.")
        return 0
    print(f"{len(strings)} scritte; aggiornati: {', '.join(stale) or 'nessuno'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
