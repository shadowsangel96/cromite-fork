#!/usr/bin/env python3
"""Una tappa della build Windows sui runner GitHub (windows-2025-vs2026: Visual Studio 2026).

Due comandi, due step del workflow:
- prepare: installa il Windows SDK che chiede Chromium se il runner non ce l'ha (a ogni tappa,
  perché ogni tappa è una macchina nuova). Nella tappa 1 scarica anche Chromium con gclient
  (gli stessi strumenti che usa Cromite), applica Cromite e le nostre patch e lancia gn gen.
- build: compila con ninja finché resta tempo nel job (GitHub lo ferma a 6 ore), poi si
  ferma pulita lasciando il resto alla tappa successiva, che riparte dalla cartella salvata
  come artifact.

Variabili: STAGE (1, 2, ...), JOB_STARTED (epoch di inizio job), FORK_BUILD_DIR, GITHUB_OUTPUT.
Output di build: finished=true quando il pacchetto è pronto in dist/, finished=false se serve
un'altra tappa.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("FORK_BUILD_DIR", r"C:\b"))
CHECKOUT = WORK / "chromium"
SRC = CHECKOUT / "src"
DEPOT = WORK / "depot_tools"
OUT = "out/win64"
DIST = ROOT / "dist"
FORKCTL = [sys.executable, str(ROOT / "tools" / "forkctl.py")]
VSWHERE = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
VS_YEARS = {"17": "2022", "18": "2026"}  # versione principale di Visual Studio -> anno
KITS = Path(r"C:\Program Files (x86)\Windows Kits\10")
# Installer del Windows SDK per la versione che Chromium chiede in build/vs_toolchain.py
# (SDK_VERSION). Link da learn.microsoft.com/windows/apps/windows-sdk/downloads; la build
# esatta è quella di docs/windows_build_instructions.md. Quando Chromium cambia SDK, la tappa 1
# si ferma subito con l'elenco da aggiornare.
SDK_INSTALLERS = {
    "10.0.28000.0": ("10.0.28000.2270", "https://go.microsoft.com/fwlink/?linkid=2370315"),
}
SDK_FEATURES = ["OptionId.DesktopCPPx64", "OptionId.DesktopCPPx86", "OptionId.WindowsDesktopDebuggers"]
JOB_LIMIT_MIN = 350  # GitHub ferma il job a 360 minuti
RESERVE_MIN = 60     # tempo lasciato per comprimere e caricare lo stato
# Stessa lista di file della release Windows di Cromite (.github/workflows/release.yaml).
PACKAGE_FILES = ["chrome.dll", "chrome.exe", "chrome_100_percent.pak", "chrome_200_percent.pak",
                 "chrome_elf.dll", "chrome_proxy.exe", "chrome_pwa_launcher.exe", "chrome_wer.dll",
                 "d3dcompiler_47.dll", "elevation_service.exe", "First Run", "icudtl.dat", "libEGL.dll",
                 "libGLESv2.dll", "Logo.png", "notification_helper.exe", "resources.pak", "SmallLogo.png",
                 "snapshot_blob.bin", "VkICD_mock_icd.dll", "VkLayer_khronos_validation.dll",
                 "vk_swiftshader.dll", "vulkan-1.dll"]
REQUIRED = {"chrome.exe", "chrome.dll", "chrome_elf.dll", "resources.pak", "icudtl.dat"}


def say(msg):
    print(f"\n==> {msg}", flush=True)


def run(cmd, cwd=None, env=None):
    print("+ " + " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True)


def set_output(key, value):
    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"{key}={value}\n")


def find_vs():
    """(anno, cartella) del Visual Studio più recente con ATL/MFC, che serve a Chromium."""
    out = subprocess.run([str(VSWHERE), "-latest", "-products", "*", "-requires",
                          "Microsoft.VisualStudio.Component.VC.ATLMFC", "-format", "json"],
                         capture_output=True, text=True, check=True).stdout
    found = json.loads(out)
    if not found:
        sys.exit("nessun Visual Studio con ATL/MFC sul runner")
    major = found[0]["installationVersion"].split(".")[0]
    if major not in VS_YEARS:
        sys.exit(f"Visual Studio {found[0]['installationVersion']} non previsto da VS_YEARS")
    return VS_YEARS[major], Path(found[0]["installationPath"])


def build_env():
    year, path = find_vs()
    env = dict(os.environ)
    env.update({"DEPOT_TOOLS_WIN_TOOLCHAIN": "0", f"vs{year}_install": str(path),
                "PATH": str(DEPOT) + os.pathsep + env["PATH"], "PYTHONUTF8": "1"})
    return env


def installed_sdks():
    include = KITS / "Include"
    return sorted(p.name for p in include.glob("10.*")) if include.exists() else []


def diagnostics():
    say("macchina")
    for drive in ("C:\\", "D:\\"):
        if Path(drive).exists():
            usage = shutil.disk_usage(drive)
            print(f"{drive} libero {usage.free / 2**30:.0f} GB su {usage.total / 2**30:.0f} GB")
    print(f"CPU: {os.cpu_count()}")
    print(f"cartella di lavoro: {WORK}")
    year, path = find_vs()
    print(f"Visual Studio {year}: {path}")
    print(f"Windows SDK: {', '.join(installed_sdks()) or 'nessuno'}")


def ensure_toolchain():
    """Controlla Visual Studio e installa il Windows SDK richiesto dal sorgente di Chromium."""
    text = (SRC / "build" / "vs_toolchain.py").read_text()
    sdk = re.search(r"^SDK_VERSION = '([\d.]+)'", text, re.M).group(1)
    versions = re.search(r"^MSVS_VERSIONS = .*?^\)", text, re.M | re.S).group(0)
    accepted = re.findall(r"\('(\d{4})', '[\d.]+'\)", versions)
    year, _ = find_vs()
    say(f"toolchain: Chromium vuole Visual Studio {' o '.join(accepted)} e Windows SDK {sdk}")
    if year not in accepted:
        sys.exit(f"sul runner c'è Visual Studio {year}: cambia runs-on in build-windows-stage.yml")
    debuggers = KITS / "Debuggers" / "x64" / "dbghelp.dll"
    if (KITS / "Include" / sdk / "um" / "windows.h").exists() and debuggers.exists():
        print("Windows SDK già installato")
        return
    if sdk not in SDK_INSTALLERS:
        sys.exit(f"Windows SDK {sdk} assente sul runner: aggiungi il suo installer a SDK_INSTALLERS "
                 f"in tools/build/gh_windows_stage.py")
    build, url = SDK_INSTALLERS[sdk]
    setup = WORK / "sdk" / "winsdksetup.exe"
    setup.parent.mkdir(parents=True, exist_ok=True)
    say(f"installo Windows SDK {build}")
    urllib.request.urlretrieve(url, setup)
    log = setup.parent / "install.log"
    started = time.time()
    code = subprocess.run([str(setup), "/features", *SDK_FEATURES, "/quiet", "/norestart",
                           "/ceip", "off", "/log", str(log)]).returncode
    print(f"installer finito in {(time.time() - started) / 60:.0f} minuti (codice {code})")
    ok = (KITS / "Include" / sdk / "um" / "windows.h").exists() and debuggers.exists()
    if code not in (0, 3010) or not ok:
        if log.exists():
            print("\n".join(log.read_text(errors="replace").splitlines()[-40:]))
        sys.exit(f"installazione del Windows SDK {build} non riuscita")


def setup_git():
    # Chromium e le patch vogliono i file così come sono: niente conversione CRLF su Windows
    for key, value in (("core.autocrlf", "false"), ("core.longpaths", "true"),
                       ("core.fscache", "true"), ("core.preloadindex", "true")):
        run(["git", "config", "--global", key, value])
    for key, value in (("user.name", "forkctl"), ("user.email", "forkctl@localhost")):
        if subprocess.run(["git", "config", "--global", key], capture_output=True).returncode:
            run(["git", "config", "--global", key, value])


def setup_depot_tools(env):
    if not (DEPOT / ".git").exists():
        say("depot_tools")
        run(["git", "clone", "--depth", "1",
             "https://chromium.googlesource.com/chromium/tools/depot_tools.git", DEPOT])
    # il primo avvio prepara python3/git di depot_tools (serve anche a gn e ninja)
    run(["cmd", "/c", DEPOT / "gclient.bat", "--version"], env=env)


def fetch_source():
    version = json.loads((ROOT / "upstream.lock").read_text())["chromium_version"]
    say(f"scarico Chromium {version}")
    SRC.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q"], cwd=SRC)
    run(["git", "remote", "add", "origin", "https://chromium.googlesource.com/chromium/src.git"], cwd=SRC)
    run(["git", "fetch", "--depth", "2", "origin", f"+refs/tags/{version}:refs/tags/{version}"], cwd=SRC)
    run(["git", "checkout", "-q", "-f", version], cwd=SRC)


def sync_and_patch(env):
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SRC, capture_output=True, text=True,
                         check=True).stdout.strip()
    (CHECKOUT / ".gclient").write_text(
        'solutions = [{\n'
        '  "name": "src",\n'
        f'  "url": "https://chromium.googlesource.com/chromium/src.git@{sha}",\n'
        '  "deps_file": "DEPS",\n'
        '  "managed": False,\n'
        # profili PGO: il blocco Windows di cromite.gn_args usa chrome_pgo_phase = 2
        '  "custom_vars": {"checkout_telemetry_dependencies": False, "checkout_pgo_profiles": True},\n'
        '}]\n'
        'target_os = ["win"]\n')
    run(["cmd", "/c", DEPOT / "gclient.bat", "sync", "-D", "--no-history", "--nohooks"], cwd=CHECKOUT, env=env)
    run(["cmd", "/c", DEPOT / "gclient.bat", "runhooks"], cwd=CHECKOUT, env=env)

    say("patch di Cromite e nostre")
    run([*FORKCTL, "apply-cromite", "--src", SRC], env=env)
    run([*FORKCTL, "apply", "--src", SRC], env=env)

    say("gn gen")
    (SRC / OUT).mkdir(parents=True, exist_ok=True)
    args = subprocess.run([*FORKCTL, "gn-args", "--os", "win", "--cpu", "x64"], capture_output=True,
                          text=True, check=True, env=env).stdout
    (SRC / OUT / "args.gn").write_text(args)
    print(args)
    run([SRC / "buildtools" / "win" / "gn.exe", "gen", OUT], cwd=SRC, env=env)


def ninja(env, deadline):
    """True se la build è finita, False se il tempo della tappa è scaduto."""
    exe = SRC / "third_party" / "ninja" / "ninja.exe"
    cmd = [str(exe), "-C", OUT, "-j", str(os.cpu_count() or 4), "chrome"]
    remaining = deadline - time.time()
    say(f"ninja per al massimo {remaining / 60:.0f} minuti")
    if remaining < 600:
        return False
    proc = subprocess.Popen(cmd, cwd=SRC, env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        code = proc.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        say("tempo della tappa finito: fermo ninja")
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        try:
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)])
            proc.wait()
        return False
    if code != 0:
        sys.exit(f"ninja fallito (codice {code})")
    return True


def package():
    name = json.loads((ROOT / "fork.json").read_text())["name"]
    out = SRC / OUT
    pkg = DIST / f"{name}-windows-x64"
    shutil.rmtree(pkg, ignore_errors=True)
    pkg.mkdir(parents=True)
    say("impacchetto")
    for f in [*PACKAGE_FILES, *(p.name for p in out.glob("*.manifest"))]:
        source = out / f
        if not source.exists() and f == "d3dcompiler_47.dll":
            source = KITS / "Redist" / "D3D" / "x64" / f
        if source.exists():
            shutil.copy2(source, pkg / f)
        elif f in REQUIRED:
            sys.exit(f"manca {f} nella build")
        else:
            print(f"(non prodotto: {f})")
    shutil.copytree(out / "locales", pkg / "locales")
    archive = shutil.make_archive(str(pkg), "zip", DIST, pkg.name)
    print(f"pronto: {archive}")


def prepare(stage):
    setup_git()
    if stage == 1:
        diagnostics()
        fetch_source()
    elif not (SRC / OUT / "build.ninja").exists():
        sys.exit("stato della tappa precedente mancante")
    # prima del lungo gclient sync: se manca un pezzo del toolchain la tappa 1 si ferma subito
    ensure_toolchain()
    env = build_env()
    setup_depot_tools(env)
    if stage == 1:
        sync_and_patch(env)


def build():
    deadline = int(os.environ["JOB_STARTED"]) + (JOB_LIMIT_MIN - RESERVE_MIN) * 60
    finished = ninja(build_env(), deadline)
    if finished:
        package()
    set_output("finished", "true" if finished else "false")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        prepare(int(os.environ["STAGE"]))
    elif command == "build":
        build()
    else:
        sys.exit("uso: gh_windows_stage.py prepare|build")


if __name__ == "__main__":
    main()
