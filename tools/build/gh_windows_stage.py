#!/usr/bin/env python3
"""Una tappa della build Windows sui runner GitHub (Windows Server 2022, Visual Studio 2022).

La tappa 1 scarica Chromium con gclient (gli stessi strumenti che usa Cromite), applica
Cromite e le nostre patch e avvia ninja. Le tappe successive ripartono dalla cartella che
la precedente ha salvato come artifact. Ogni tappa compila finché resta tempo nel job
(GitHub lo ferma a 6 ore), poi si ferma pulita lasciando il resto alla successiva.

Variabili: STAGE (1, 2, ...), JOB_STARTED (epoch di inizio job), GITHUB_OUTPUT.
Output: finished=true quando il pacchetto è pronto in dist/, finished=false se serve
un'altra tappa.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("FORK_BUILD_DIR", r"C:\b"))
CHECKOUT = WORK / "chromium"
SRC = CHECKOUT / "src"
DEPOT = WORK / "depot_tools"
OUT = "out/win64"
DIST = ROOT / "dist"
FORKCTL = [sys.executable, str(ROOT / "tools" / "forkctl.py")]
VS = Path(r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise")
KITS = Path(r"C:\Program Files (x86)\Windows Kits\10")
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


def build_env():
    env = dict(os.environ)
    env.update({"DEPOT_TOOLS_WIN_TOOLCHAIN": "0", "vs2022_install": str(VS),
                "PATH": str(DEPOT) + os.pathsep + env["PATH"], "PYTHONUTF8": "1"})
    return env


def diagnostics():
    say("macchina")
    for drive in ("C:\\", "D:\\"):
        if Path(drive).exists():
            usage = shutil.disk_usage(drive)
            print(f"{drive} libero {usage.free / 2**30:.0f} GB su {usage.total / 2**30:.0f} GB")
    print(f"CPU: {os.cpu_count()}")
    print(f"Visual Studio: {VS.exists()} ({VS})")
    sdks = sorted(p.name for p in (KITS / "Include").glob("10.*")) if (KITS / "Include").exists() else []
    print(f"Windows SDK: {', '.join(sdks) or 'nessuno'}")
    debuggers = KITS / "Debuggers" / "x64" / "dbghelp.dll"
    print(f"Debugging Tools: {debuggers.exists()}")
    if not VS.exists() or not debuggers.exists():
        sys.exit("manca Visual Studio 2022 o i Debugging Tools del Windows SDK sul runner")


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


def fetch_and_patch(env):
    lock = json.loads((ROOT / "upstream.lock").read_text())
    version = lock["chromium_version"]
    say(f"scarico Chromium {version}")
    SRC.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q"], cwd=SRC)
    run(["git", "remote", "add", "origin", "https://chromium.googlesource.com/chromium/src.git"], cwd=SRC)
    run(["git", "fetch", "--depth", "2", "origin", f"+refs/tags/{version}:refs/tags/{version}"], cwd=SRC)
    run(["git", "checkout", "-q", "-f", version], cwd=SRC)
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


def main():
    stage = int(os.environ["STAGE"])
    deadline = int(os.environ["JOB_STARTED"]) + (JOB_LIMIT_MIN - RESERVE_MIN) * 60
    env = build_env()
    setup_git()
    if stage == 1:
        diagnostics()
    setup_depot_tools(env)
    if stage == 1:
        fetch_and_patch(env)
    elif not (SRC / OUT / "build.ninja").exists():
        sys.exit("stato della tappa precedente mancante")
    finished = ninja(env, deadline)
    if finished:
        package()
    set_output("finished", "true" if finished else "false")


if __name__ == "__main__":
    main()
