#!/usr/bin/env python3
"""L'icona di Shannon: la bussola con l'anello argilla (idea A della tela), chiara e scura.

Scrive in overlay/, sopra le icone di Chromium e di Cromite:
  chrome/app/theme/chromium/mac/AppIcon.icon/   sorgente di Icon Composer: macOS 26 e successivi
                                                 (chiara, scura, colorata, trasparente)
  chrome/app/theme/chromium/mac/Assets.car      AppIcon.icon compilato con actool
  chrome/app/theme/chromium/mac/app.icns        per i macOS più vecchi
  chrome/app/theme/chromium/product_logo*       la bussola senza riquadro, nelle pagine del browser
  chrome/app/theme/default_{100,200}_percent/chromium/product_logo_{16,32}.png
  components/resources/default_{100,200}_percent/chromium/product_logo{,_white}.png
                                                 logo con il nome (chrome://version)
  chrome/app/theme/chromium/win/chromium.ico e win/tiles/*.png   Windows
Le immagini sono già nel repo: lo script serve solo quando cambia il disegno.
Serve un Mac con Xcode (actool, iconutil, sips).
uso: tools/genera_icona.py [--anteprima <cartella>]
"""
import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / "overlay"
THEME = OVERLAY / "chrome/app/theme"
NAME = json.loads((ROOT / "fork.json").read_text())["name"]

CLAY, IVORY = "#D97757", "#FAF9F5"
# Colori dei due temi. "base": il fondo del riquadro (dall'alto in basso), "faccia": il quadrante.
THEMES = {
    "chiaro": {"base": ("#FFFFFF", "#EEEBE2"), "faccia": ("#FBFAF6", "#EFECE2"), "sud": "#3D3D3A",
               "punti": ("#3D3D3A", 0.4), "perno": IVORY, "riflesso": 0.6, "bordo": ("#000000", 0.07)},
    "scuro": {"base": ("#33322F", "#1B1B19"), "faccia": ("#2E2D2A", "#23221F"), "sud": "#E8E6DC",
              "punti": ("#E8E6DC", 0.5), "perno": "#141413", "riflesso": 0.1, "bordo": ("#FFFFFF", 0.12)},
}


def gradient(gid, top, bottom):
    return (f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient>')


def needle(theme, s, part=("nord", "sud")):
    """L'ago, girato di 30 gradi. s: la scala (1 = ago lungo 250 su una tela di 1024)."""
    c = 512
    length, width = 250 * s, 52 * s
    paths = {
        "nord": (f"M{c},{c - length:.2f} Q{c + width * 1.15:.2f},{c - length * 0.18:.2f} {c + width:.2f},{c} "
                 f"L{c - width:.2f},{c} Q{c - width * 1.15:.2f},{c - length * 0.18:.2f} {c},{c - length:.2f} Z", CLAY),
        "sud": (f"M{c},{c + length:.2f} Q{c + width * 1.15:.2f},{c + length * 0.18:.2f} {c + width:.2f},{c} "
                f"L{c - width:.2f},{c} Q{c - width * 1.15:.2f},{c + length * 0.18:.2f} {c},{c + length:.2f} Z",
                THEMES[theme]["sud"]),
    }
    body = "".join(f'<path d="{paths[p][0]}" fill="{paths[p][1]}" stroke="{paths[p][1]}" '
                   f'stroke-width="{10 * s:.2f}" stroke-linejoin="round"/>' for p in part)
    return f'<g transform="rotate(30 {c} {c})">{body}</g>'


def pin(theme, s):
    return (f'<circle cx="512" cy="512" r="{30 * s:.2f}" fill="{THEMES[theme]["perno"]}" stroke="{CLAY}" '
            f'stroke-width="{10 * s:.2f}"/>')


def dots(theme, distance, size):
    color, opacity = THEMES[theme]["punti"]
    return "".join(f'<circle cx="{512 + dx * distance:.2f}" cy="{512 + dy * distance:.2f}" r="{size:.2f}" '
                   f'fill="{color}" fill-opacity="{opacity}"/>' for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)))


def face(theme, radius, ring, gid="faccia"):
    top, bottom = THEMES[theme]["faccia"]
    return (f'<defs>{gradient(gid, top, bottom)}</defs><circle cx="512" cy="512" r="{radius:.2f}" '
            f'fill="url(#{gid})" stroke="{CLAY}" stroke-width="{ring:.2f}"/>')


def compass(theme, k):
    """La bussola intera, centrata su una tela di 1024. k = 1: le misure del Mac sulla tela."""
    return (face(theme, 300 * k, 26 * k) + dots(theme, 240 * k, 15 * k) + needle(theme, 0.92 * k)
            + pin(theme, 0.92 * k))


def svg(body, size=1024, view=1024, width=None):
    w = width or size
    vw = view * w / size
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{size}" '
            f'viewBox="0 0 {vw:g} {view}">{body}</svg>')


def mac_tile(theme):
    """Il riquadro arrotondato come nella tela (824 su 1024), per app.icns."""
    t = THEMES[theme]
    edge, opacity = t["bordo"]
    return (f'<defs>{gradient("base", *t["base"])}'
            f'<linearGradient id="riflesso" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="#FFFFFF" stop-opacity="{t["riflesso"]}"/>'
            f'<stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient></defs>'
            f'<rect x="100" y="100" width="824" height="824" rx="186" fill="url(#base)"/>'
            f'<rect x="101" y="101" width="822" height="822" rx="185" fill="none" stroke="{edge}" '
            f'stroke-opacity="{opacity}" stroke-width="2"/>'
            f'<path d="M286 100 H738 A186 186 0 0 1 924 286 V330 C 760 400 264 400 100 330 V286 '
            f'A186 186 0 0 1 286 100 Z" fill="url(#riflesso)"/>'
            + compass(theme, 1))


def flat(theme="chiaro"):
    """La bussola senza riquadro (Windows e pagine del browser): più grande sulla tela."""
    return compass(theme, 380 / 300)


def render(svg_text, out, tmp):
    src = Path(tmp) / (out.stem + ".svg")
    src.write_text(svg_text)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(out)], check=True,
                   capture_output=True)


def rgb(hex_color, alpha=1.0):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return f"srgb:{r:.5f},{g:.5f},{b:.5f},{alpha:.5f}"


def icon_composer(dest):
    """AppIcon.icon: fondo, quadrante e ago in livelli separati; il sistema aggiunge il vetro
    e fa le versioni colorata e trasparente. Misure della tela scalate sul riquadro intero."""
    k = 1024 / 824
    assets = dest / "Assets"
    if dest.exists():
        shutil.rmtree(dest)
    assets.mkdir(parents=True)
    layers = {}
    for theme in THEMES:
        top, bottom = THEMES[theme]["faccia"]
        layers[f"faccia_{theme}"] = (f'<defs>{gradient("f", top, bottom)}</defs>'
                                     f'<circle cx="512" cy="512" r="{300 * k:.2f}" fill="url(#f)"/>')
        layers[f"punti_{theme}"] = dots(theme, 240 * k, 15 * k)
        layers[f"sud_{theme}"] = needle(theme, 0.92 * k, part=("sud",))
        layers[f"perno_{theme}"] = pin(theme, 0.92 * k)
    layers["anello"] = (f'<circle cx="512" cy="512" r="{300 * k:.2f}" fill="none" stroke="{CLAY}" '
                        f'stroke-width="{26 * k:.2f}"/>')
    layers["nord"] = needle("chiaro", 0.92 * k, part=("nord",))
    for name, body in layers.items():
        (assets / f"{name}.svg").write_text(svg(body) + "\n")

    def layer(name, themed=False):
        entry = {"name": name, "glass": False}
        if themed:
            entry["image-name-specializations"] = [
                {"value": f"{name}_chiaro.svg"}, {"appearance": "dark", "value": f"{name}_scuro.svg"}]
        else:
            entry["image-name"] = f"{name}.svg"
        return entry

    def base(theme):
        top, bottom = THEMES[theme]["base"]
        return {"linear-gradient": [rgb(top), rgb(bottom)]}

    doc = {
        "fill-specializations": [{"value": base("chiaro")}, {"appearance": "dark", "value": base("scuro")}],
        "groups": [
            {"name": "ago", "layers": [layer("perno", True), layer("nord"), layer("sud", True)],
             "shadow": {"kind": "neutral", "opacity": 0.5}, "specular": True,
             "translucency": {"enabled": False, "value": 0.5}},
            {"name": "quadrante", "layers": [layer("punti", True), layer("anello"), layer("faccia", True)],
             "shadow": {"kind": "neutral", "opacity": 0.3}, "specular": True,
             "translucency": {"enabled": False, "value": 0.5}},
        ],
        "supported-platforms": {"squares": "shared"},
    }
    (dest / "icon.json").write_text(json.dumps(doc, indent=2) + "\n")


def compile_assets(icon, out_dir, tmp):
    """Assets.car da AppIcon.icon, come Chromium (le immagini per i macOS vecchi le fa actool)."""
    build = Path(tmp) / "actool"
    build.mkdir()
    result = subprocess.run(
        ["xcrun", "actool", "--output-format", "human-readable-text", "--notices", "--warnings", "--errors",
         "--platform", "macosx", "--target-device", "mac", "--minimum-deployment-target", "13.0",
         "--app-icon", "AppIcon", "--output-partial-info-plist", str(build / "partial.plist"),
         "--compile", str(build), str(icon)], capture_output=True, text=True)
    if result.returncode or not (build / "Assets.car").is_file():
        sys.exit("actool:\n" + result.stdout + result.stderr)
    if "warning" in result.stdout.lower():
        print(result.stdout)
    shutil.copy(build / "Assets.car", out_dir / "Assets.car")


def icns(theme, out, tmp):
    iconset = Path(tmp) / "app.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            render(svg(mac_tile(theme), size=px), iconset / name, tmp)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def ico(pngs, out):
    """Un .ico con le immagini in PNG (Windows Vista e successivi)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, data = b"", b""
    for size, blob in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + entries + data)


def wordmark(white):
    """Logo con il nome, per chrome://version (171 x 32 a 1x)."""
    color = "#FFFFFF" if white else "#3D3D3A"
    logo = f'<g transform="scale({32 / 1024})">{flat()}</g>'
    text = (f'<text x="42" y="23.5" font-family="-apple-system, Helvetica Neue, sans-serif" '
            f'font-size="20" font-weight="500" fill="{color}">{NAME}</text>')
    return logo + text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anteprima", help="scrive qui anche le anteprime grandi (PNG)")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        mac = THEME / "chromium/mac"
        mac.mkdir(parents=True, exist_ok=True)
        icon_composer(mac / "AppIcon.icon")
        compile_assets(mac / "AppIcon.icon", mac, tmp)
        icns("chiaro", mac / "app.icns", tmp)

        chromium = THEME / "chromium"
        (chromium / "product_logo.svg").write_text(svg(flat(), size=256) + "\n")
        for size in (16, 24, 48, 64, 128, 256):
            render(svg(flat(), size=size), chromium / f"product_logo_{size}.png", tmp)
        for folder, scale in (("default_100_percent", 1), ("default_200_percent", 2)):
            for size in (16, 32):
                render(svg(flat(), size=size * scale), THEME / folder / "chromium" / f"product_logo_{size}.png",
                       tmp)
            res = OVERLAY / "components/resources" / folder / "chromium"
            for white in (False, True):
                name = "product_logo_white.png" if white else "product_logo.png"
                render(svg(wordmark(white), size=32 * scale, view=32, width=171 * scale), res / name, tmp)

        win = chromium / "win"
        pngs = []
        for size in (16, 24, 32, 48, 64, 128, 256):
            out = Path(tmp) / f"ico_{size}.png"
            render(svg(flat(), size=size), out, tmp)
            pngs.append((size, out.read_bytes()))
        ico(pngs, win / "chromium.ico")
        render(svg(flat(), size=150), win / "tiles/Logo.png", tmp)
        render(svg(flat(), size=70), win / "tiles/SmallLogo.png", tmp)

        if args.anteprima:
            dest = Path(args.anteprima)
            dest.mkdir(parents=True, exist_ok=True)
            for theme in THEMES:
                render(svg(mac_tile(theme), size=512), dest / f"mac_{theme}.png", tmp)
            render(svg(flat(), size=256), dest / "logo.png", tmp)
            for white in (False, True):
                render(svg(wordmark(white), size=64, view=32, width=342), dest / f"nome_{int(white)}.png", tmp)
    print("Icone scritte in overlay/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
