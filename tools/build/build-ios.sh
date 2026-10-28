#!/usr/bin/env bash
# Build iOS. Su iOS Chromium usa WebKit (regola Apple), non Blink: quasi nessuna patch di
# Cromite ha effetto qui, e le funzioni (adblock, privacy) vanno reimplementate nel codice
# ios/ con patch "ios: ...". Produce un .ipa NON firmato: per installarlo serve firmarlo
# (Apple Developer, oppure AltStore/SideStore con il proprio Apple ID).
# Requisiti host: Mac Apple Silicon, Xcode completo con SDK iOS, ~32 GB RAM, ~200 GB disco.
source "$(dirname "$0")/common.sh"

[ "$(uname -s)" = Darwin ] || die "iOS si compila solo su un Mac"
xcodebuild -version >/dev/null 2>&1 || die "serve Xcode completo con l'SDK iOS"

prepare_source "" 'target_os = ["ios"]
target_os_only = True'

OUT=out/ios
gn_gen ios arm64 "$OUT"
build "$OUT" chrome

say "impacchetto"
APP="$(find "$SRC/$OUT" -maxdepth 1 -name '*.app' -type d | head -1)"
[ -n "$APP" ] || die "nessuna .app in $SRC/$OUT"
STAGE="$(mktemp -d)"
mkdir -p "$STAGE/Payload" "$DIST"
cp -R "$APP" "$STAGE/Payload/"
IPA="$DIST/$FORK_NAME-ios-unsigned.ipa"
rm -f "$IPA"
(cd "$STAGE" && zip -qr "$IPA" Payload)
rm -rf "$STAGE"
say "pronto: $IPA"
