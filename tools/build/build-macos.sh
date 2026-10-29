#!/usr/bin/env bash
# Build macOS (arm64 di default, FORK_MAC_CPU=x64 per Intel). Cromite non supporta macOS:
# le correzioni per farlo compilare e funzionare vanno in patches/macos/ (titolo "mac: ...").
# Requisiti host: Mac Apple Silicon, Xcode completo, ~250 GB su volume APFS. Con poca RAM
# (8-16 GB) limitare i processi con FORK_JOBS=4: più lenta, ma senza riempire lo swap.
source "$(dirname "$0")/common.sh"

[ "$(uname -s)" = Darwin ] || die "macOS si compila solo su un Mac"
xcodebuild -version >/dev/null 2>&1 || die "serve Xcode completo (non solo i Command Line Tools)"
CPU="${FORK_MAC_CPU:-arm64}"

prepare_source "" 'target_os = ["mac"]'

OUT="out/mac-$CPU"
gn_gen mac "$CPU" "$OUT"
# FORK_SOURCE_ONLY=1: scarica, applica le patch e lancia gn, senza compilare (la parte che usa
# la rete si fa quando la banda è libera; la compilazione non scarica niente)
if [ "${FORK_SOURCE_ONLY:-0}" = 1 ]; then
  say "sorgenti e gn pronti, compilazione saltata (FORK_SOURCE_ONLY=1)"
  exit 0
fi
build "$OUT" chrome

say "impacchetto"
APP="$SRC/$OUT/$(product_name).app"
[ -d "$APP" ] || die "app non trovata: $APP"
# Firma ad-hoc: si avvia su questo Mac; per distribuirla ad altri serve un Developer ID + notarizzazione.
codesign --force --deep --sign - "$APP"
mkdir -p "$DIST"
ZIP="$DIST/$FORK_NAME-macos-$CPU.zip"
rm -f "$ZIP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
say "pronto: $ZIP"
