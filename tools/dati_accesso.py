#!/usr/bin/env python3
"""I cookie di analisi e pubblicitari per l'assistente all'accesso, da Open Cookie Database.

uso: dati_accesso.py <open-cookie-database.csv> <commit del repository>
Scrive overlay/chrome/browser/fork/login_cookie_data.inc, che login_assistant.cc include.
Il CSV si scarica da https://github.com/jkwakman/Open-Cookie-Database (Apache-2.0, licenza in
licenze/open-cookie-database/LICENSE); qui si legge soltanto.

Il database dice per ogni cookie l'azienda e spesso il dominio dove sta. Per non scambiare per
pubblicitaria la sessione di un sito qualsiasi:
- valgono su ogni sito i nomi senza dominio (_ga, _gcl_au...) e quelli che iniziano con "_" e
  sono lunghi almeno 4 caratteri (_fbp, _ttp...: li salvano gli script sul sito stesso);
- i nomi esatti valgono su ogni sito solo se lunghi almeno 4 caratteri (3 se iniziano con "_",
  come _ga), i prefissi solo se lunghi almeno 4: quelli più corti (fid, dst...) sono troppo comuni;
- tutti gli altri (SID, uid, datr...) valgono solo sul dominio dell'azienda.
Un nome con più categorie conta come pubblicitario prima che come analisi.
"""
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "overlay", "chrome", "browser", "fork", "login_cookie_data.inc")
KIND = {"Marketing": "kAdvertising", "Analytics": "kAnalytics"}
RANK = {"kAdvertising": 0, "kAnalytics": 1}
NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z:]+$")  # caratteri ammessi nei nomi dei cookie
DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


def domain_of(text):
    """'.bing.com (3rd party) or .microsoft.com' -> 'bing.com'."""
    first = text.strip().lower().split(" ")[0].split("(")[0].split(",")[0].split("/")[0]
    first = first.lstrip(".*")
    return first if DOMAIN.match(first) else ""


def main(csv_path, commit):
    generic, prefixes, vendor = {}, {}, {}
    for row in csv.DictReader(open(csv_path, encoding="utf-8")):
        kind = KIND.get(row["Category"].strip())
        raw = row["Cookie / Data Key name"].strip()
        name = raw.rstrip("*")
        if not kind or not NAME.match(name):
            continue
        wildcard = row["Wildcard match"].strip() == "1" or raw.endswith("*")
        domain = domain_of(row["Domain"])
        anywhere = (not domain or (name.startswith("_") and len(name) >= 4)) and \
                   (not wildcard or len(name) >= 4) and \
                   (len(name) >= 4 or (name.startswith("_") and len(name) >= 3))
        if anywhere:
            table, key = (prefixes if wildcard else generic), name
        elif domain:
            table, key = vendor, (name, domain, wildcard)
        else:
            continue
        if key not in table or RANK[kind] < RANK[table[key]]:
            table[key] = kind

    def lit(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = [
        "// Generato da tools/dati_accesso.py: non modificare a mano.",
        "// Fonte: Open Cookie Database, https://github.com/jkwakman/Open-Cookie-Database",
        f"// (commit {commit}), licenza Apache-2.0 in licenze/open-cookie-database/LICENSE",
        "// del repository del fork. Solo i cookie di analisi e pubblicitari, convertiti.",
        "",
        "// Nomi esatti validi su ogni sito, in ordine per la ricerca binaria.",
        "inline constexpr KnownCookie kGenericCookies[] = {",
    ]
    for name in sorted(generic, key=lambda s: s.encode()):
        lines.append(f"    {{{lit(name)}, CookieKind::{generic[name]}}},")
    lines += ["};", "", "// Prefissi validi su ogni sito.", "inline constexpr KnownCookie kGenericCookiePrefixes[] = {"]
    for name in sorted(prefixes, key=lambda s: s.encode()):
        lines.append(f"    {{{lit(name)}, CookieKind::{prefixes[name]}}},")
    lines += ["};", "", "// Cookie validi solo sul dominio dell'azienda (e i suoi sottodomini).",
              "inline constexpr VendorCookie kVendorCookies[] = {"]
    for (name, domain, wildcard) in sorted(vendor, key=lambda k: (k[1], k[0].encode(), k[2])):
        kind = vendor[(name, domain, wildcard)]
        lines.append(f"    {{{lit(name)}, {lit(domain)}, CookieKind::{kind}, {'true' if wildcard else 'false'}}},")
    lines.append("};")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{len(generic)} nomi generici, {len(prefixes)} prefissi, {len(vendor)} legati a un dominio -> "
          f"{os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
