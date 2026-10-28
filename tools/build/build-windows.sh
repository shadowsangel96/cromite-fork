#!/usr/bin/env bash
# Build Windows x64 in cross-compilazione da Linux x86-64, come fa Cromite.
# Requisiti host: Linux x86-64, ~32 GB RAM, ~250 GB disco, pacchetti msitools e zip.
# ACCEPT_MSVC_LICENSE=1 è obbligatorio: il toolchain MSVC/Windows SDK si scarica con
# msvc-wine, e farlo significa accettare la licenza di Microsoft Visual Studio.
source "$(dirname "$0")/common.sh"

[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || die "Windows si compila da Linux x86-64"
[ "${ACCEPT_MSVC_LICENSE:-}" = 1 ] || die "imposta ACCEPT_MSVC_LICENSE=1 dopo aver letto la licenza di Visual Studio"
command -v msiextract >/dev/null || die "manca msitools (sudo apt install msitools)"
command -v zip >/dev/null || die "manca zip (sudo apt install zip)"

# Toolchain come tools/images/win-sdk/ di Cromite: stesse componenti, stesso gen-setenv.py.
TOOLCHAIN="$WORKDIR/win_sdk/out"
if [ ! -f "$WORKDIR/win_sdk/.ready" ]; then
  say "scarico il toolchain MSVC + Windows SDK"
  mkdir -p "$WORKDIR/win_sdk"
  cd "$WORKDIR/win_sdk"
  [ -d msvc-wine ] || git clone --depth 1 https://github.com/mstorsjo/msvc-wine.git
  msvc-wine/vsdownload.py --accept-license --major 17 --cache msvc-cache --dest ./out --save-manifest \
    Microsoft.VisualStudio.Component.VC.14.44.17.14.x86.x64 \
    Microsoft.VisualStudio.Component.VC.14.44.17.14.MFC \
    Microsoft.VisualStudio.Component.Windows11SDK.26100
  curl -fsSL -o gen-setenv.py \
    "https://raw.githubusercontent.com/uazo/cromite/$CROMITE_TAG/tools/images/win-sdk/gen-setenv.py"
  python3 gen-setenv.py ./out/
  touch .ready
fi
export DEPOT_TOOLS_WIN_TOOLCHAIN_BASE_URL="$TOOLCHAIN/"
export GYP_MSVS_OVERRIDE_PATH="$TOOLCHAIN/"
export WINDOWSSDKDIR="$TOOLCHAIN/Windows Kits/10/"

# Stesse esclusioni e hook disattivati del .gclient di Cromite per il cross-build.
prepare_source '  "custom_deps": {
    "src/third_party/apache-windows-arm64": None,
    "src/third_party/updater/chrome_win_x86": None,
    "src/third_party/updater/chrome_win_x86_64": None,
    "src/third_party/updater/chromium_win_x86": None,
    "src/third_party/updater/chromium_win_x86_64": None,
    "src/third_party/gperf": None,
    "src/third_party/lighttpd": None,
    "src/third_party/lzma_sdk/bin/host_platform": None,
    "src/third_party/lzma_sdk/bin/win64": None,
    "src/third_party/perl": None,
    "src/tools/skia_goldctl/win": None,
    "src/third_party/screen-ai/windows_amd64": None,
    "src/third_party/screen-ai/windows_386": None,
  },
  "custom_hooks": [
    {"name": "ciopfs_linux", "pattern": ".", "action": ["echo", "ciopfs_linux hook override"]},
    {"name": "win_toolchain", "pattern": ".", "action": ["echo", "win_toolchain hook override"]},
    {"name": "rc_win", "pattern": ".", "action": ["echo", "rc_win hook override"]},
    {"name": "rc_linux", "pattern": ".", "action": ["echo", "rc_linux hook override"]},
    {"name": "apache_win32", "pattern": ".", "action": ["echo", "apache_win32 hook override"]},
  ],' 'target_os = ["win"]'

OUT=out/win64
gn_gen win x64 "$OUT"
build "$OUT" chrome

say "impacchetto"
PKG="$DIST/$FORK_NAME-windows-x64"
rm -rf "$PKG" "$PKG.zip"
mkdir -p "$PKG"
cd "$SRC/$OUT"
# Stessa lista di file della release Windows di Cromite (.github/workflows/release.yaml).
cp ./*.manifest chrome.dll chrome.exe chrome_100_percent.pak chrome_200_percent.pak chrome_elf.dll \
   chrome_proxy.exe chrome_pwa_launcher.exe chrome_wer.dll elevation_service.exe "First Run" icudtl.dat \
   libEGL.dll libGLESv2.dll Logo.png notification_helper.exe resources.pak SmallLogo.png \
   snapshot_blob.bin VkICD_mock_icd.dll VkLayer_khronos_validation.dll vk_swiftshader.dll vulkan-1.dll "$PKG/"
cp "$TOOLCHAIN/Windows Kits/10/Redist/D3D/x64/d3dcompiler_47.dll" "$PKG/"
cp -r locales "$PKG/"
(cd "$DIST" && zip -qr "$FORK_NAME-windows-x64.zip" "$FORK_NAME-windows-x64")
say "pronto: $PKG.zip"
