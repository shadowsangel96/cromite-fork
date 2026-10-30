#!/usr/bin/env bash
# Funzioni comuni agli script di build: `source tools/build/common.sh`.
#
# Variabili utili:
#   FORK_BUILD_DIR   dove tenere depot_tools e i sorgenti (default ~/fork-build; ~100 GB per versione)
#   FORK_DIST_DIR    dove mettere i pacchetti finali (default <repo>/dist)
#   FORK_KEEP_OLD=1  non cancellare i sorgenti delle versioni precedenti
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FORKCTL=(python3 "$ROOT/tools/forkctl.py")
VERSION="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["chromium_version"])' "$ROOT/upstream.lock")"
CROMITE_TAG="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["cromite_tag"])' "$ROOT/upstream.lock")"
FORK_NAME="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/fork.json")"
WORKDIR="${FORK_BUILD_DIR:-$HOME/fork-build}"
DIST="${FORK_DIST_DIR:-$ROOT/dist}"
DEPOT="$WORKDIR/depot_tools"
CHECKOUT="$WORKDIR/chromium-$VERSION"
SRC="$CHECKOUT/src"
export PATH="$DEPOT:$PATH" DEPOT_TOOLS_UPDATE=0

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31merrore:\033[0m %s\n' "$*" >&2; exit 1; }

fetch_depot_tools() {
  if [ ! -d "$DEPOT/.git" ]; then
    say "scarico depot_tools"
    git clone --depth 1 https://chromium.googlesource.com/chromium/tools/depot_tools.git "$DEPOT"
  fi
  # con DEPOT_TOOLS_UPDATE=0 gclient non prepara il python3 di depot_tools, che serve a gn
  if [ ! -f "$DEPOT/python3_bin_reldir.txt" ]; then
    say "preparo il python di depot_tools"
    (source "$DEPOT/bootstrap_python3" && bootstrap_python3)
  fi
}

remove_old_checkouts() {
  [ "${FORK_KEEP_OLD:-0}" = 1 ] && return
  local dir
  for dir in "$WORKDIR"/chromium-*; do
    if [ -d "$dir" ] && [ "$dir" != "$CHECKOUT" ]; then
      say "rimuovo i sorgenti vecchi $dir (FORK_KEEP_OLD=1 per tenerli)"
      rm -rf "$dir"
    fi
  done
}

# fetch_chromium <solution_extra> <gclient_extra>: checkout di Chromium $VERSION senza
# storia, come tools/images/chr-source/prepare-build.sh di Cromite. <solution_extra> sono
# voci aggiunte al dizionario della soluzione "src" (custom_deps, custom_hooks),
# <gclient_extra> righe in fondo a .gclient (target_os, ecc.).
fetch_chromium() {
  local solution_extra="$1" extra="$2"
  if [ -f "$CHECKOUT/.forkctl-synced" ]; then
    say "sorgenti Chromium $VERSION già scaricati"
    return
  fi
  remove_old_checkouts
  say "scarico Chromium $VERSION"
  mkdir -p "$SRC"
  cd "$SRC"
  [ -d .git ] || git init -q
  git remote get-url origin >/dev/null 2>&1 || git remote add origin https://chromium.googlesource.com/chromium/src.git
  git fetch --depth 2 origin "+refs/tags/$VERSION:refs/tags/$VERSION"
  git checkout -q -f "$VERSION"
  cat > "$CHECKOUT/.gclient" <<EOF
solutions = [{
  "name": "src",
  "url": "https://chromium.googlesource.com/chromium/src.git@$(git rev-parse HEAD)",
  "deps_file": "DEPS",
  "managed": False,
  "custom_vars": {"checkout_telemetry_dependencies": False},
$solution_extra
}]
$extra
EOF
  git submodule foreach --quiet 'git config -f .git/config submodule.$name.ignore all' || true
  gclient sync -D --no-history --nohooks
  gclient runhooks
  touch "$CHECKOUT/.forkctl-synced"
}

# prepare_source <solution_extra> <gclient_extra>: Chromium + patch Cromite (una volta per
# versione) + le nostre patch, riapplicate da zero a ogni build.
prepare_source() {
  fetch_depot_tools
  fetch_chromium "$1" "$2"
  cd "$SRC"
  git config user.name >/dev/null || git config user.name "forkctl"
  git config user.email >/dev/null || git config user.email "forkctl@localhost"
  if ! git rev-parse -q --verify refs/tags/forkctl-cromite >/dev/null; then
    say "applico le patch di Cromite"
    "${FORKCTL[@]}" apply-cromite --src "$SRC"
    git tag forkctl-cromite
  fi
  # Stesse patch dell'ultima volta: niente reset, così i file non cambiano orario e ninja non
  # ricompila quello che dipende da loro (una build interrotta riparte da dove era).
  local stamp
  stamp="$(cd "$ROOT" && find patches overlay -type f | LC_ALL=C sort | xargs shasum | shasum | cut -c1-40)"
  if [ "$(cat "$CHECKOUT/.forkctl-applied" 2>/dev/null)" = "$stamp" ]; then
    say "le nostre patch sono già applicate"
    return
  fi
  say "applico le nostre patch"
  rm -f "$CHECKOUT/.forkctl-applied"
  "${FORKCTL[@]}" reapply --src "$SRC" --base refs/tags/forkctl-cromite
  echo "$stamp" > "$CHECKOUT/.forkctl-applied"
}

# gn_gen <os> <cpu> <out>
gn_gen() {
  mkdir -p "$SRC/$3"
  "${FORKCTL[@]}" gn-args --os "$1" --cpu "$2" > "$SRC/$3/args.gn"
  (cd "$SRC" && gn gen "$3")
}

# build <out> <target...>
# FORK_JOBS: processi in parallelo (meno = meno RAM). FORK_KEEP_GOING=1: dopo un errore continua
# con il resto, così una piattaforma nuova mostra tutti i suoi errori in una build sola.
build() {
  local out="$1" extra=()
  shift
  [ -n "${FORK_JOBS:-}" ] && extra+=(-j "$FORK_JOBS")
  [ "${FORK_KEEP_GOING:-0}" = 1 ] && extra+=(-k 0)
  (cd "$SRC" && autoninja ${extra[@]+"${extra[@]}"} -C "$out" "$@")
}

product_name() {
  sed -n 's/^PRODUCT_FULLNAME=//p' "$SRC/chrome/app/theme/chromium/BRANDING"
}
