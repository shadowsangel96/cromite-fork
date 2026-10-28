#!/usr/bin/env python3
"""forkctl: mantiene le nostre modifiche sopra Cromite e le riapplica a ogni aggiornamento.

Le modifiche vivono in patches/ come file `git format-patch`, nell'ordine di
patches/series. Ogni patch porta il trailer `Fork-Patch: <percorso>` che la lega
al suo file.

Per lavorare sulle patch non serve il checkout completo di Chromium: forkctl
ricostruisce un "mini-albero" con i soli file che le nostre patch toccano,
scaricati da Gitiles alla versione di Chromium usata da Cromite, e ci applica
sopra le patch di Cromite che li riguardano. Un aggiornamento è un vero rebase
git a 3 vie fra il mini-albero vecchio e quello nuovo; git rerere ricorda le
risoluzioni dei conflitti in state/rr-cache e le riapplica da solo.

Regola: nessuna patch viene persa in silenzio. Se una patch non si riapplica,
o diventa vuota perché Cromite/Chromium contengono già la modifica, forkctl si
ferma e chiede una decisione esplicita.
"""

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "fork.json").read_text())
PATCHES = ROOT / "patches"
SERIES = PATCHES / "series"
RETIRED = PATCHES / "retired"
OVERLAY = ROOT / "overlay"
GN_ARGS = ROOT / "gn_args"
LOCK = ROOT / "upstream.lock"
RR_CACHE = ROOT / "state" / "rr-cache"
APPLY_SCRIPT_HASH = ROOT / "state" / "cromite-apply-script.sha256"
REPORTS = ROOT / "reports"
CACHE = Path(os.environ.get("FORKCTL_CACHE", ROOT / ".cache"))
WORK = Path(os.environ.get("FORKCTL_WORK", ROOT / ".work"))
# riferimenti nel repo di lavoro, sempre completi: "base" è anche una cartella di Chromium
BASE, BASE_OLD, BASE_NEW = "refs/tags/forkctl-base", "refs/tags/forkctl-base-old", "refs/tags/forkctl-base-new"
FORK = "refs/heads/fork"

EXIT_OK, EXIT_ERROR, EXIT_CONFLICT, EXIT_EMPTY = 0, 1, 2, 3

TAG_RE = re.compile(r"^v(\d+(?:\.\d+){3})-([0-9a-f]{40})$")
TRAILER_RE = re.compile(r"^Fork-Patch:\s*(\S+)\s*$", re.M)
MARKER_RE = re.compile(rb"^(<<<<<<<|>>>>>>>)( |$)", re.M)
AREAS = {"win": "windows", "windows": "windows", "mac": "macos", "macos": "macos", "ios": "ios"}
# Sottomoduli che lo script di Cromite incorpora in src prima di applicare le patch
# (tools/images/cromite-source/apply-cromite-patches.sh); usato se lo script non si legge.
DEFAULT_FLATTENED = ["v8", "third_party/devtools-frontend/src", "third_party/skia",
                     "third_party/perfetto", "third_party/boringssl/src"]
REPORT_LIMIT = 60000  # i body delle issue GitHub si fermano a 65536 caratteri


class ForkError(Exception):
    pass


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------- git

GIT_ENV = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_AUTHOR_NAME",
                        "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")}


def git(*args, cwd, check=True, stdin=None, env=None):
    res = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True,
                         env={**GIT_ENV, **(env or {})})
    if check and res.returncode != 0:
        raise ForkError(f"git {' '.join(map(str, args))} fallito:\n"
                        f"{res.stderr.decode(errors='replace')}{res.stdout.decode(errors='replace')}")
    return res


def text(res):
    return res.stdout.decode("utf-8", "surrogateescape").strip()


def git_version():
    m = re.search(r"(\d+)\.(\d+)", text(git("--version", cwd=ROOT)))
    return int(m.group(1)), int(m.group(2))


def patch_author():
    """Identità pubblica con cui firmiamo le patch (fork.json), mai quella del computer."""
    return CONFIG["patch_author"]["name"], CONFIG["patch_author"]["email"]


def identity_args(repo):
    """-c user.name/email se il repo non ha già un'identità (serve a git am/commit)."""
    name, email = patch_author()
    args = []
    if git("config", "user.name", cwd=repo, check=False).returncode:
        args += ["-c", f"user.name={name}"]
    if git("config", "user.email", cwd=repo, check=False).returncode:
        args += ["-c", f"user.email={email}"]
    return args


def init_repo(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    git("init", "-q", "-b", "fork", cwd=path)
    settings = {"commit.gpgsign": "false", "core.autocrlf": "false", "core.safecrlf": "false",
                "core.quotePath": "false", "rerere.enabled": "true", "rerere.autoUpdate": "true",
                "advice.detachedHead": "false", "merge.conflictStyle": "diff3"}
    for key, value in settings.items():
        git("config", key, value, cwd=path)
    # i commit fatti qui diventano patch pubbliche: sempre l'identità di fork.json
    name, email = patch_author()
    git("config", "user.name", name, cwd=path)
    git("config", "user.email", email, cwd=path)


def commit(repo, message):
    git("commit", "-q", "--allow-empty", "--no-verify", "-m", message, cwd=repo)
    return text(git("rev-parse", "HEAD", cwd=repo))


def restore_rr_cache(repo):
    if RR_CACHE.is_dir():
        shutil.copytree(RR_CACHE, repo / ".git" / "rr-cache", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".gitkeep"))


def save_rr_cache(repo):
    src = repo / ".git" / "rr-cache"
    if src.is_dir():
        RR_CACHE.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, RR_CACHE, dirs_exist_ok=True)


def rebase_in_progress(repo):
    return (repo / ".git" / "rebase-merge").exists() or (repo / ".git" / "rebase-apply").exists()


def has_markers(path):
    return path.is_file() and MARKER_RE.search(path.read_bytes()) is not None


# -------------------------------------------------------------------- http

def http_get(url, *, allow_404=False, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "forkctl", **(headers or {})})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 and allow_404:
                return None
            retriable, err = e.code in (429, 500, 502, 503, 504), f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            retriable, err = True, str(e)
        if not retriable or attempt == 5:
            raise ForkError(f"download fallito ({err}): {url}")
        time.sleep(min(2 ** attempt, 30))


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Gitiles:
    """Lettura di file e alberi da un repo Gitiles, con cache su disco (le revisioni sono fisse)."""

    def __init__(self, base):
        self.base = base.rstrip("/").removesuffix(".git")
        self._trees = {}
        self._lock = threading.Lock()

    def _cached(self, rev, path, fmt):
        key = hashlib.sha256(f"{self.base}|{rev}|{path}|{fmt}".encode()).hexdigest()
        entry = CACHE / "gitiles" / key[:2] / key
        missing = entry.with_suffix(".404")
        if entry.exists():
            return entry.read_bytes()
        if missing.exists():
            return None
        url = f"{self.base}/+/{rev}/{urllib.parse.quote(path)}?format={fmt}"
        data = http_get(url, allow_404=True)
        write_atomic(missing if data is None else entry, b"" if data is None else data)
        return data

    def file(self, rev, path):
        data = self._cached(rev, path, "TEXT")
        if data is None:
            raise ForkError(f"{self.base}: {path}@{rev} non trovato")
        return base64.b64decode(data)

    def tree(self, rev, path):
        """{nome: {"mode", "type", "id"}} della directory `path` ({} se non esiste)."""
        with self._lock:
            if (rev, path) in self._trees:
                return self._trees[(rev, path)]
        data = self._cached(rev, path, "JSON")
        entries = {}
        if data is not None:
            listing = json.loads(data.decode().split("\n", 1)[1])  # salta il prefisso )]}'
            entries = {e["name"]: e for e in listing.get("entries", [])}
        with self._lock:
            self._trees[(rev, path)] = entries
        return entries


class ChromiumSource:
    """Contenuto dei file di Chromium a una versione, compresi i sottomoduli che Cromite incorpora."""

    def __init__(self, version, flattened):
        self.version = version
        self.rev = f"refs/tags/{version}"
        self.flattened = set(flattened)
        self.local = os.environ.get("FORKCTL_CHROMIUM_DIR")
        self.src = Gitiles(CONFIG["chromium_gitiles"])
        self._submodules = None
        self._repos = {}
        self._lock = threading.Lock()

    def submodules(self):
        with self._lock:
            if self._submodules is None:
                subs, current = {}, {}
                data = self.src.file(self.rev, ".gitmodules").decode()
                for line in data.splitlines():
                    line = line.strip()
                    if line.startswith("[submodule"):
                        current = {}
                    elif "=" in line:
                        key, value = (part.strip() for part in line.split("=", 1))
                        current[key] = value
                        if "path" in current and "url" in current:
                            subs[current["path"]] = current["url"]
                self._submodules = subs
            return self._submodules

    def _locate(self, path):
        owner = max((s for s in self.submodules() if path.startswith(s + "/")), key=len, default=None)
        if owner is None:
            return self.src, self.rev, path
        if owner not in self.flattened:
            raise ForkError(f"{path} sta nel sottomodulo {owner}: Cromite non lo incorpora in src, "
                            "quindi git am non può modificarlo")
        parent, name = posixpath.split(owner)
        link = self.src.tree(self.rev, parent).get(name)
        if not link or link["type"] != "commit":
            raise ForkError(f"gitlink {owner} non trovato in Chromium {self.version}")
        url = self.submodules()[owner]
        with self._lock:
            repo = self._repos.setdefault(owner, Gitiles(url))
        return repo, link["id"], path[len(owner) + 1:]

    def get(self, path):
        """-> (contenuto, modo git) oppure (None, None) se il file non esiste."""
        if self.local:
            f = Path(self.local) / self.version / path
            if f.is_symlink():
                return os.readlink(f).encode(), "120000"
            if not f.is_file():
                return None, None
            return f.read_bytes(), "100755" if os.access(f, os.X_OK) else "100644"
        repo, rev, rel = self._locate(path)
        parent, name = posixpath.split(rel)
        entry = repo.tree(rev, parent).get(name)
        if entry is None or entry["type"] != "blob":
            return None, None
        return repo.file(rev, rel), format(entry["mode"], "o")


# ----------------------------------------------------------------- cromite

class Cromite:
    """Checkout (solo build/ e lo script di applicazione) di un tag di Cromite."""

    def __init__(self, tag):
        m = TAG_RE.match(tag)
        if not m:
            raise ForkError(f"tag Cromite non valido: {tag} (atteso v<versione>-<sha>)")
        self.tag = tag
        self.dir = self._checkout(tag)
        self.version = (self.dir / "build" / "RELEASE").read_text().strip()
        if self.version != m.group(1):
            raise ForkError(f"{tag}: build/RELEASE dice {self.version}")

    @staticmethod
    def _checkout(tag):
        local = os.environ.get("FORKCTL_CROMITE_DIR")
        if local:
            path = Path(local) / tag
            if not (path / "build" / "RELEASE").is_file():
                raise ForkError(f"{path}: Cromite locale non trovato")
            return path
        dest = CACHE / "cromite" / tag
        if (dest / "build" / "RELEASE").is_file():
            return dest
        tmp = dest.with_name(tag + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        log(f"scarico Cromite {tag} (solo build/)...")
        git("clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", "--single-branch",
            "--branch", tag, CONFIG["cromite_repo"], str(tmp), cwd=ROOT)
        git("sparse-checkout", "set", "build", "tools/images/cromite-source", cwd=tmp)
        os.replace(tmp, dest)
        return dest

    def patch_list(self):
        lines = (self.dir / "build" / "cromite_patches_list.txt").read_text().splitlines()
        return [line.strip() for line in lines if line.strip().endswith(".patch")
                and not line.strip().startswith("#")]

    def patch_path(self, name):
        return self.dir / "build" / "patches" / name

    def apply_script(self):
        return self.dir / "tools" / "images" / "cromite-source" / "apply-cromite-patches.sh"

    def flattened(self):
        script = self.apply_script()
        if script.is_file():
            found = re.findall(r"rm -rf\s+(\S+?)/\.git\b", script.read_text())
            if found:
                return found
        return DEFAULT_FLATTENED

    def gn_args(self):
        return (self.dir / "build" / "cromite.gn_args").read_text()


def read_lock():
    return json.loads(LOCK.read_text())


def write_lock(lock):
    LOCK.write_text(json.dumps(lock, indent=2) + "\n")


def lock_for(tag):
    return {"cromite_tag": tag, "chromium_version": Cromite(tag).version}


def latest_tag():
    if os.environ.get("FORKCTL_LATEST_TAG"):
        return os.environ["FORKCTL_LATEST_TAG"]
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    url = f"https://api.github.com/repos/{CONFIG['cromite_github']}/releases?per_page=50"
    releases = json.loads(http_get(url, headers=headers))
    stable = [r["tag_name"] for r in releases
              if not r["draft"] and not r["prerelease"] and TAG_RE.match(r["tag_name"])]
    if not stable:
        raise ForkError("nessuna release stabile di Cromite trovata")
    return max(stable, key=lambda t: tuple(int(x) for x in TAG_RE.match(t).group(1).split(".")))


# ----------------------------------------------------------------- patches

def _unquote(value):
    """Toglie le virgolette in stile C che git usa per i percorsi con caratteri speciali."""
    if not value.startswith('"'):
        return value
    raw, out, i = value[1:-1].encode("utf-8", "surrogateescape"), bytearray(), 0
    escapes = {b"n": b"\n", b"t": b"\t", b'"': b'"', b"\\": b"\\", b"a": b"\a",
               b"b": b"\b", b"f": b"\f", b"r": b"\r", b"v": b"\v"}
    while i < len(raw):
        if raw[i] == 0x5C and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in b"01234567":
                out.append(int(raw[i + 1:i + 4], 8))
                i += 4
                continue
            out += escapes.get(nxt, nxt)
            i += 2
            continue
        out.append(raw[i])
        i += 1
    return out.decode("utf-8", "surrogateescape")


def _diff_path(value, strip_prefix):
    value = value.split("\t")[0]
    if value == "/dev/null":
        return None
    value = _unquote(value)
    return value[2:] if strip_prefix and value[1:2] == "/" else value


def _header_path(header):
    """Percorso dalla riga `diff --git a/P b/P` (per patch binarie o di soli permessi)."""
    if header.startswith('"'):
        return _diff_path(header[:header.index('"', 1) + 1], True)
    half = (len(header) - 5) // 2
    candidate = header[2:2 + half]
    if header == f"a/{candidate} b/{candidate}":
        return candidate
    return header.split(" b/", 1)[0][2:]


def patch_files(data):
    """[(vecchio, nuovo)] per ogni file della patch; None = creato/cancellato."""
    pairs = []
    for block in re.split(rb"^(?=diff --git )", data, flags=re.M)[1:]:
        lines = block.split(b"\n")
        header = lines[0][len(b"diff --git "):].decode("utf-8", "surrogateescape")
        old = new = default = _header_path(header)
        for raw in lines[1:]:
            if raw.startswith((b"@@", b"GIT binary patch")):
                break
            line = raw.decode("utf-8", "surrogateescape")
            if line.startswith("--- "):
                old = _diff_path(line[4:], True)
            elif line.startswith("+++ "):
                new = _diff_path(line[4:], True)
            elif line.startswith(("rename from ", "copy from ")):
                old = _unquote(line.split(" from ", 1)[1])
            elif line.startswith(("rename to ", "copy to ")):
                new = _unquote(line.split(" to ", 1)[1])
            elif line.startswith("new file mode"):
                old = None
            elif line.startswith("deleted file mode"):
                new = None
        pairs.append((old, new) if (old, new) != (None, None) else (default, default))
    return pairs


def read_series():
    lines = SERIES.read_text().splitlines() if SERIES.exists() else []
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def write_series(entries):
    header = ("# Ordine di applicazione delle nostre patch, sopra Cromite.\n"
              "# Generato da `forkctl export`: ogni patch porta il trailer Fork-Patch.\n")
    SERIES.write_text(header + "".join(f"{rel}\n" for rel in entries))


def check_series(series):
    problems = []
    if len(set(series)) != len(series):
        problems.append("patch duplicate in series")
    for rel in series:
        path = PATCHES / rel
        if not path.is_file():
            problems.append(f"{rel}: elencata in series ma il file non esiste")
            continue
        header = path.read_text(errors="replace").split("\ndiff --git ", 1)[0]
        found = TRAILER_RE.findall(header)
        if found != [rel]:
            problems.append(f"{rel}: trailer Fork-Patch mancante o diverso ({found})")
        author = re.search(r"^From: (.*)$", header, re.M)
        expected = "{} <{}>".format(*patch_author())
        if not author or author.group(1).strip() != expected:
            problems.append(f"{rel}: autore diverso da patch_author di fork.json "
                            "(rigenera la patch con forkctl export)")
    listed = set(series)
    for path in sorted(PATCHES.rglob("*.patch")):
        rel = path.relative_to(PATCHES).as_posix()
        if not rel.startswith("retired/") and rel not in listed:
            problems.append(f"{rel}: patch non elencata in series (verrebbe ignorata)")
    if problems:
        raise ForkError("serie di patch non coerente:\n  " + "\n  ".join(problems))


def series_paths(series):
    paths = set()
    for rel in series:
        for pair in patch_files((PATCHES / rel).read_bytes()):
            paths.update(p for p in pair if p)
    return paths


def _include(path):
    return "--include=" + re.sub(r"([*?\[\\])", r"\\\1", path)


def materialize(cromite, paths):
    """Contenuto dei file `paths` dopo Chromium + patch di Cromite: {path: (bytes|None, mode)}."""
    infos = [(name, patch_files(cromite.patch_path(name).read_bytes())) for name in cromite.patch_list()]
    need = set(paths)
    for _, pairs in reversed(infos):  # i file rinominati/copiati servono col nome di partenza
        for old, new in pairs:
            if new in need and old and old != new:
                need.add(old)
    relevant = []
    for name, pairs in infos:
        touched = sorted({p for pair in pairs for p in pair if p} & need)
        if touched:
            relevant.append((name, touched))

    source = ChromiumSource(cromite.version, cromite.flattened())
    ordered = sorted(need)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        contents = dict(zip(ordered, pool.map(source.get, ordered)))

    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE, prefix="mat-") as tmp:
        repo = Path(tmp)
        init_repo(repo)
        write_tree(repo, contents)
        for name, touched in relevant:
            res = git("apply", "--index", "--whitespace=nowarn", *map(_include, touched),
                      str(cromite.patch_path(name)), cwd=repo, check=False)
            if res.returncode:
                raise ForkError(f"la patch Cromite {name} non si applica a {touched} su Chromium "
                                f"{cromite.version}:\n{res.stderr.decode(errors='replace')}")
        modes = {}
        for entry in text(git("ls-files", "-s", cwd=repo)).splitlines():
            meta, path = entry.split("\t", 1)
            modes[path] = meta.split()[0]
        result = {}
        for path in paths:
            f = repo / path
            if f.is_symlink():
                result[path] = (os.readlink(f).encode(), "120000")
            elif f.is_file():
                result[path] = (f.read_bytes(), modes.get(path, "100644"))
            else:
                result[path] = (None, None)
        return result


def write_tree(repo, files, *, wipe=True):
    if wipe:
        for child in repo.iterdir():
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    for path, (data, mode) in sorted(files.items()):
        if data is None:
            continue
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            os.symlink(data.decode(), f)
        else:
            f.write_bytes(data)
            if mode == "100755":
                f.chmod(0o755)
    git("add", "-A", "-f", ".", cwd=repo)


def apply_series(repo, series, *, three_way=False):
    for rel in series:
        res = git(*identity_args(repo), "am", "--keep-cr", "--quiet", *(["-3"] if three_way else []),
                  str(PATCHES / rel), cwd=repo, check=False)
        if res.returncode:
            git("am", "--abort", cwd=repo, check=False)
            raise ForkError(f"la patch {rel} non si applica:\n{res.stderr.decode(errors='replace')}"
                            f"{res.stdout.decode(errors='replace')}")


def trailer_of(repo, rev):
    message = text(git("log", "-1", "--format=%B", rev, cwd=repo))
    m = TRAILER_RE.search(message)
    return m.group(1) if m else None


def subject_of(repo, rev):
    return text(git("log", "-1", "--format=%s", rev, cwd=repo))


def add_trailer(mail, rel):
    """Aggiunge `Fork-Patch: rel` al messaggio di una patch in formato mail."""
    head_end = mail.index(b"\n\n") + 2
    sep = mail.index(b"\ndiff --git ", head_end - 2)
    body = mail[head_end:sep + 1].strip(b"\n")
    body = body + b"\n\n" if body else b""
    return mail[:head_end] + body + f"Fork-Patch: {rel}\n\n".encode() + mail[sep + 1:]


def new_patch_name(subject, taken):
    m = re.match(r"^(win|windows|mac|macos|ios)\s*:\s*(.*)$", subject, re.I)
    area = AREAS[m.group(1).lower()] if m else "common"
    title = m.group(2) if m else subject
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:60].rstrip("-") or "patch"
    numbers = [int(n) for n in re.findall(rf"^{area}/(\d+)-", "\n".join(taken), re.M)]
    return f"{area}/{max(numbers, default=0) + 1:04d}-{slug}.patch"


def export(repo, base, *, allow_remove=False, dropped=()):
    """Riscrive patches/ e series dai commit base..HEAD del repo di lavoro."""
    commits = text(git("rev-list", "--reverse", f"{base}..HEAD", cwd=repo)).split()
    old_series = read_series()
    taken, entries = set(), []
    for sha in commits:
        subject = subject_of(repo, sha)
        mail = git("format-patch", "-1", "--stdout", "--no-signature", "--zero-commit",
                   "--full-index", "--binary", "--no-stat", "-N", sha, cwd=repo).stdout
        if b"\ndiff --git " not in mail:
            raise ForkError(f"il commit '{subject}' non contiene modifiche")
        rel = trailer_of(repo, sha)
        if rel is None:
            rel = new_patch_name(subject, taken | set(old_series))
            mail = add_trailer(mail, rel)
        if rel in taken:
            raise ForkError(f"due commit dichiarano la stessa patch {rel}")
        taken.add(rel)
        entries.append((rel, mail))
    removed = [rel for rel in old_series if rel not in taken and rel not in dropped]
    if removed and not allow_remove:
        raise ForkError("l'export toglierebbe queste patch: " + ", ".join(removed) +
                        "\nSe è voluto, ripeti con --allow-remove (finiranno in patches/retired/).")
    for rel in [*removed, *dropped]:
        if (PATCHES / rel).is_file():
            (RETIRED / rel).parent.mkdir(parents=True, exist_ok=True)
            os.replace(PATCHES / rel, RETIRED / rel)
    for rel, mail in entries:
        write_atomic(PATCHES / rel, mail)
    write_series([rel for rel, _ in entries])
    return [rel for rel, _ in entries], removed


# -------------------------------------------------------------- work state

def state_file():
    return WORK / ".git" / "forkctl.json"


def load_state():
    if not state_file().is_file():
        raise ForkError("nessuna sessione di lavoro aperta (.work): usa `forkctl workspace` o `forkctl update`")
    return json.loads(state_file().read_text())


def save_state(state):
    state_file().write_text(json.dumps(state, indent=2) + "\n")


def guard_open_session(force):
    if force or not state_file().is_file():
        return
    state = json.loads(state_file().read_text())
    if state.get("mode") == "update" and not state.get("done"):
        raise ForkError("c'è un aggiornamento in corso in .work: completa con `forkctl continue` "
                        "o annulla con `forkctl abort` (oppure --force)")
    if state.get("mode") == "workspace":
        head = text(git("rev-parse", "HEAD", cwd=WORK, check=False))
        dirty = text(git("status", "--porcelain", cwd=WORK, check=False))
        if head != state.get("applied") or dirty:
            raise ForkError("il workspace in .work ha modifiche non esportate: `forkctl export` "
                            "(o --force per buttarle)")


def build_work(cromite, paths, series, base_tag):
    files = materialize(cromite, paths)
    init_repo(WORK)
    restore_rr_cache(WORK)
    write_tree(WORK, files)
    commit(WORK, f"upstream: Cromite {cromite.tag} (Chromium {cromite.version})")
    git("update-ref", base_tag, "HEAD", cwd=WORK)
    apply_series(WORK, series)
    return files


# ------------------------------------------------------------------ report

def write_report(state, outcome, details=""):
    new, old = state["new"], state["old"]
    lines = [f"# Aggiornamento a Cromite {new['chromium_version']}", "",
             f"- da: `{old['cromite_tag']}` (Chromium {old['chromium_version']})",
             f"- a: `{new['cromite_tag']}` (Chromium {new['chromium_version']})",
             f"- esito: **{outcome}**", ""]
    if state.get("apply_script_changed"):
        lines += ["> ⚠️ Cromite ha cambiato `apply-cromite-patches.sh`: verificare che "
                  "`forkctl apply-cromite` faccia ancora le stesse cose.", ""]
    lines += ["| patch | stato |", "|---|---|"]
    for rel in state["series"]:
        if rel in state.get("dropped", []):
            status = "ritirata (già contenuta upstream)"
        elif rel in state.get("resolved_manual", []):
            status = "conflitto risolto a mano"
        elif rel in state.get("resolved_auto", []):
            status = "conflitto risolto in automatico (rerere)"
        elif rel == state.get("stopped_at"):
            status = "**da risolvere**"
        else:
            status = "riapplicata"
        lines.append(f"| `{rel}` | {status} |")
    body = "\n".join(lines) + "\n" + details
    if len(body) > REPORT_LIMIT:
        body = body[:REPORT_LIMIT] + "\n\n…(troncato)\n"
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"update-{new['chromium_version']}.md"
    path.write_text(body)
    return path


def conflict_details(rel, files):
    diff = text(git("diff", cwd=WORK, check=False))
    return (f"\n## Conflitto nella patch `{rel}`\n\nFile in conflitto: "
            + ", ".join(f"`{f}`" for f in files)
            + "\n\nPer risolvere:\n\n```bash\npython3 tools/forkctl.py update --to "
            + "<tag>\n# sistema i file in .work/, poi\npython3 tools/forkctl.py continue\n```\n\n"
            + "```diff\n" + diff[:40000] + "\n```\n")


# ------------------------------------------------------------------ update

def drive_rebase(state):
    """Fa avanzare il rebase finché serve un umano o finché è finito."""
    while rebase_in_progress(WORK):
        rel = trailer_of(WORK, "REBASE_HEAD") or subject_of(WORK, "REBASE_HEAD")
        unmerged = text(git("diff", "--name-only", "--diff-filter=U", cwd=WORK)).splitlines()
        staged = text(git("diff", "--cached", "--name-only", cwd=WORK)).splitlines()
        marked = [f for f in staged if has_markers(WORK / f)]
        if unmerged or marked:
            state["stopped_at"] = rel
            save_state(state)
            report = write_report(state, "conflitto", conflict_details(rel, unmerged or marked))
            log(f"\nCONFLITTO nella patch {rel}: {', '.join(unmerged or marked)}")
            log(f"Risolvi i file in {WORK}, poi: python3 tools/forkctl.py continue")
            log(f"Report: {report}")
            return EXIT_CONFLICT
        if git("diff", "--cached", "--quiet", "HEAD", cwd=WORK, check=False).returncode == 0:
            state["stopped_at"] = rel
            state["empty"] = rel
            save_state(state)
            report = write_report(state, "patch diventata vuota",
                                  f"\nLa patch `{rel}` non cambia più nulla: Cromite/Chromium "
                                  "contengono già la modifica. Controlla e, se è così, ritirala con "
                                  "`forkctl continue --drop-empty`.\n")
            log(f"\nLa patch {rel} è diventata vuota (modifica già presente upstream).")
            log("Se è corretto: python3 tools/forkctl.py continue --drop-empty")
            log(f"Report: {report}")
            return EXIT_EMPTY
        # nessun conflitto aperto: rerere ha già riapplicato una risoluzione salvata
        log(f"  {rel}: conflitto risolto con una risoluzione salvata (rerere)")
        state.setdefault("resolved_auto", []).append(rel)
        save_state(state)
        git("rebase", "--continue", cwd=WORK, check=False, env={"GIT_EDITOR": "true"})
    return finalize(state)


def finalize(state):
    expected = [rel for rel in state["series"] if rel not in state.get("dropped", [])]
    commits = text(git("rev-list", "--reverse", f"{BASE_NEW}..{FORK}", cwd=WORK)).split()
    found = [trailer_of(WORK, sha) for sha in commits]
    if found != expected:
        raise ForkError(f"dopo il rebase la serie non torna:\n  attese {expected}\n  trovate {found}")
    export(WORK, BASE_NEW, dropped=state.get("dropped", []))
    write_lock(state["new"])
    save_rr_cache(WORK)
    state.pop("stopped_at", None)
    rng = git("range-diff", "--no-color", f"{BASE_OLD}..{state['before']}", f"{BASE_NEW}..{FORK}",
              cwd=WORK, check=False)
    details = "\n## Confronto patch prima/dopo (git range-diff)\n\n```\n" + text(rng)[:40000] + "\n```\n"
    report = write_report(state, "ok", details)
    state["done"] = True
    save_state(state)
    log(f"\nAggiornato a {state['new']['cromite_tag']}: {len(expected)} patch riapplicate.")
    log(f"Report: {report}")
    return EXIT_OK


def check_apply_script(cromite):
    """True se lo script di applicazione di Cromite è cambiato rispetto a quello noto."""
    script = cromite.apply_script()
    if not script.is_file():
        return False
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    known = APPLY_SCRIPT_HASH.read_text().strip() if APPLY_SCRIPT_HASH.exists() else digest
    if known != digest:
        log("ATTENZIONE: Cromite ha cambiato apply-cromite-patches.sh; controlla `forkctl apply-cromite`.")
        return True
    return False


def cmd_update(args):
    guard_open_session(args.force)
    old = read_lock()
    tag = args.to or latest_tag()
    if tag == old["cromite_tag"] and not args.force:
        log(f"Già su {tag}: niente da fare.")
        return EXIT_OK
    series = read_series()
    check_series(series)
    paths = series_paths(series)
    old_cromite, new_cromite = Cromite(old["cromite_tag"]), Cromite(tag)
    log(f"Aggiornamento {old['cromite_tag']} -> {tag}: {len(series)} patch su {len(paths)} file")

    build_work(old_cromite, paths, series, BASE_OLD)
    before = text(git("rev-parse", "HEAD", cwd=WORK))
    git("switch", "-q", "-c", "upstream", BASE_OLD, cwd=WORK)
    write_tree(WORK, materialize(new_cromite, paths))
    commit(WORK, f"upstream: Cromite {tag} (Chromium {new_cromite.version})")
    git("update-ref", BASE_NEW, "HEAD", cwd=WORK)
    git("switch", "-q", "fork", cwd=WORK)

    state = {"mode": "update", "old": old, "new": {"cromite_tag": tag, "chromium_version": new_cromite.version},
             "before": before, "series": series, "resolved_auto": [], "resolved_manual": [],
             "dropped": [], "apply_script_changed": check_apply_script(new_cromite)}
    save_state(state)
    empty = "stop" if git_version() >= (2, 45) else "ask"
    git("rebase", f"--empty={empty}", "--onto", BASE_NEW, BASE_OLD, "fork",
        cwd=WORK, check=False, env={"GIT_EDITOR": "true"})
    return drive_rebase(state)


def cmd_continue(args):
    state = load_state()
    if state.get("mode") != "update" or state.get("done"):
        raise ForkError("nessun aggiornamento in corso")
    if rebase_in_progress(WORK):
        rel = trailer_of(WORK, "REBASE_HEAD") or subject_of(WORK, "REBASE_HEAD")
        if args.drop_empty:
            if state.get("empty") != rel:
                raise ForkError(f"--drop-empty vale solo per una patch diventata vuota (ora: {rel})")
            state["dropped"].append(rel)
            state.pop("empty")
            save_state(state)
            git("rebase", "--skip", cwd=WORK, check=False, env={"GIT_EDITOR": "true"})
            return drive_rebase(state)
        unmerged = text(git("diff", "--name-only", "--diff-filter=U", cwd=WORK)).splitlines()
        tracked = text(git("diff", "--name-only", "HEAD", cwd=WORK)).splitlines()
        left = [f for f in set(unmerged) | set(tracked) if has_markers(WORK / f)]
        if left:
            raise ForkError("restano marcatori di conflitto in: " + ", ".join(sorted(left)))
        git("add", "-u", cwd=WORK)
        if git("diff", "--cached", "--quiet", "HEAD", cwd=WORK, check=False).returncode == 0:
            raise ForkError(f"la patch {rel} risulterebbe vuota: se la modifica è già upstream "
                            "usa --drop-empty, altrimenti ripristina il tuo cambiamento")
        state["resolved_manual"].append(rel)
        state.pop("empty", None)
        save_state(state)
        res = git("rebase", "--continue", cwd=WORK, check=False, env={"GIT_EDITOR": "true"})
        if res.returncode and not rebase_in_progress(WORK):
            raise ForkError(res.stderr.decode(errors="replace"))
        save_rr_cache(WORK)  # la risoluzione è registrata: la salviamo subito
    return drive_rebase(state)


def cmd_abort(args):
    if WORK.exists():
        if rebase_in_progress(WORK):
            git("rebase", "--abort", cwd=WORK, check=False)
        shutil.rmtree(WORK)
    log("Sessione in .work annullata; patches/ e upstream.lock non sono stati toccati.")
    return EXIT_OK


# --------------------------------------------------------------- authoring

def cmd_workspace(args):
    guard_open_session(args.force)
    lock = read_lock()
    series = read_series()
    check_series(series)
    extra = {p.strip("/") for p in args.add}
    cromite = Cromite(lock["cromite_tag"])
    files = build_work(cromite, series_paths(series) | extra, series, BASE)
    for path in sorted(extra):
        if files[path][0] is None:
            log(f"nota: {path} non esiste upstream, lo crei tu")
    save_state({"mode": "workspace", "lock": lock, "applied": text(git("rev-parse", "HEAD", cwd=WORK))})
    log(f"\nWorkspace pronto in {WORK} (Cromite {lock['cromite_tag']}, {len(series)} patch applicate).")
    log("Modifica i file e crea un commit per ogni patch (prefisso 'win:', 'mac:' o 'ios:' nel titolo")
    log("per le patch di piattaforma), poi: python3 tools/forkctl.py export")
    return EXIT_OK


def cmd_add(args):
    state = load_state()
    if state.get("mode") != "workspace":
        raise ForkError("`add` funziona solo in un workspace (forkctl workspace)")
    if text(git("status", "--porcelain", cwd=WORK)):
        raise ForkError("prima fai commit (o stash) delle modifiche in .work")
    present = set(text(git("ls-tree", "-r", "--name-only", BASE, cwd=WORK)).splitlines())
    paths = {p.strip("/") for p in args.paths} - present
    if not paths:
        log("I file sono già nel workspace.")
        return EXIT_OK
    files = materialize(Cromite(state["lock"]["cromite_tag"]), paths)
    git("switch", "-q", "--detach", BASE, cwd=WORK)
    write_tree(WORK, files, wipe=False)
    new_base = commit(WORK, "upstream: + " + ", ".join(sorted(paths)))
    git("switch", "-q", "fork", cwd=WORK)
    res = git("rebase", "-q", "--onto", new_base, BASE, "fork", cwd=WORK, check=False)
    if res.returncode:
        git("rebase", "--abort", cwd=WORK, check=False)
        raise ForkError(f"impossibile aggiungere {sorted(paths)}: un tuo commit li crea già?")
    git("update-ref", BASE, new_base, cwd=WORK)
    state["applied"] = text(git("rev-parse", "HEAD", cwd=WORK))
    save_state(state)
    for path in sorted(paths):
        log(f"aggiunto {path}" + (" (non esiste upstream)" if files[path][0] is None else ""))
    return EXIT_OK


def cmd_export(args):
    state = load_state()
    if state.get("mode") != "workspace":
        raise ForkError("`export` funziona in un workspace; gli aggiornamenti esportano da soli")
    if text(git("status", "--porcelain", cwd=WORK)):
        raise ForkError("ci sono modifiche non committate in .work")
    entries, removed = export(WORK, BASE, allow_remove=args.allow_remove)
    # riallinea i commit del workspace alle patch scritte, così portano il trailer Fork-Patch
    git("reset", "-q", "--hard", BASE, cwd=WORK)
    apply_series(WORK, entries)
    state["applied"] = text(git("rev-parse", "HEAD", cwd=WORK))
    save_state(state)
    check_series(read_series())
    log(f"Esportate {len(entries)} patch in patches/ ({', '.join(entries)}).")
    if removed:
        log("Ritirate in patches/retired/: " + ", ".join(removed))
    return EXIT_OK


def cmd_verify(args):
    lock = read_lock()
    series = read_series()
    check_series(series)
    cromite = Cromite(lock["cromite_tag"])
    if cromite.version != lock["chromium_version"]:
        raise ForkError("upstream.lock non coerente con build/RELEASE di Cromite")
    files = materialize(cromite, series_paths(series))
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CACHE, prefix="verify-") as tmp:
        repo = Path(tmp)
        init_repo(repo)
        write_tree(repo, files)
        commit(repo, "base")
        apply_series(repo, series)
        count = int(text(git("rev-list", "--count", "HEAD", cwd=repo)))
        if count != len(series) + 1:
            raise ForkError(f"applicate {count - 1} patch su {len(series)}")
    log(f"OK: tutte le {len(series)} patch si applicano su {lock['cromite_tag']}.")
    return EXIT_OK


# --------------------------------------------------- full Chromium checkout

def check_src_version(src, lock):
    values = dict(line.split("=", 1) for line in (src / "chrome" / "VERSION").read_text().split())
    version = ".".join(values[k] for k in ("MAJOR", "MINOR", "BUILD", "PATCH"))
    if version != lock["chromium_version"]:
        raise ForkError(f"{src} è Chromium {version}, upstream.lock vuole {lock['chromium_version']}")


def rmtree_force(path):
    """Come shutil.rmtree, ma toglie la sola lettura (su Windows gli oggetti git ce l'hanno)."""
    def retry(func, target, _):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=retry)


def flatten_submodule(src, sub):
    """Come apply-cromite-patches.sh: trasforma il sottomodulo in file normali di src."""
    path = src / sub
    gitdir = path / ".git"
    if not gitdir.exists():
        log(f"  {sub}: già incorporato")
        return
    rmtree_force(gitdir) if gitdir.is_dir() else gitdir.unlink()
    tmp = path.with_name(path.name + ".forkctl-tmp")
    os.replace(path, tmp)
    git("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", sub, cwd=src)
    os.replace(tmp, path)
    git("add", "-f", "--", sub, cwd=src)
    git(*identity_args(src), "commit", "-q", "--no-verify", "-m", f":NOEXPORT: {sub} repo", cwd=src)


def cmd_apply_cromite(args):
    lock = read_lock()
    src = Path(args.src).resolve()
    check_src_version(src, lock)
    cromite = Cromite(lock["cromite_tag"])
    check_apply_script(cromite)
    for sub in cromite.flattened():
        flatten_submodule(src, sub)
    names = cromite.patch_list()
    for i, name in enumerate(names, 1):
        data = cromite.patch_path(name).read_bytes()
        data = re.sub(rb"^---", b"FILE:" + name.encode() + b"\n---", data, count=1, flags=re.M)
        res = git(*identity_args(src), "am", stdin=data, cwd=src, check=False)
        if res.returncode:
            git("am", "--abort", cwd=src, check=False)
            raise ForkError(f"patch Cromite {name} non applicabile:\n{res.stderr.decode(errors='replace')}")
        if i % 50 == 0 or i == len(names):
            log(f"  patch Cromite {i}/{len(names)}")
    return EXIT_OK


def cmd_apply(args):
    lock = read_lock()
    series = read_series()
    check_series(series)
    src = Path(args.src).resolve()
    check_src_version(src, lock)
    before = int(text(git("rev-list", "--count", "HEAD", cwd=src)))
    apply_series(src, series, three_way=True)
    after = int(text(git("rev-list", "--count", "HEAD", cwd=src)))
    if after - before != len(series):
        raise ForkError(f"applicate {after - before} patch su {len(series)}")
    copied = 0
    for f in sorted(OVERLAY.rglob("*")):
        rel = f.relative_to(OVERLAY)
        if f.is_file() and f.name not in (".gitkeep", "README.md"):
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, src / rel)
            git("add", "-f", "--", rel.as_posix(), cwd=src)
            copied += 1
    if copied:
        git(*identity_args(src), "commit", "-q", "--no-verify", "-m", "fork: overlay", cwd=src)
    log(f"Applicate {len(series)} patch e {copied} file overlay su {src}.")
    return EXIT_OK


def merged_gn_args(target_os, target_cpu):
    """args.gn = Cromite + gn_args/common.gn + gn_args/<os>.gn; i nostri valori vincono."""
    ours = []
    for name in ("common.gn", f"{target_os}.gn"):
        if (GN_ARGS / name).is_file():
            ours += (GN_ARGS / name).read_text().splitlines()
    assign = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    overridden = {m.group(1) for line in ours if (m := assign.match(line))}
    standalone = any(line.strip() == "# forkctl: senza-args-cromite" for line in ours)
    base, depth = [], 0
    if not standalone:
        for line in Cromite(read_lock()["cromite_tag"]).gn_args().splitlines():
            m = assign.match(line)
            if depth == 0 and m and m.group(1) in overridden:
                base.append(f"# (sovrascritto dal fork) {line}")
            else:
                base.append(line)
            depth += line.count("{") - line.count("}")
    return "\n".join([f'target_os = "{target_os}"', f'target_cpu = "{target_cpu}"', "",
                      "# --- Cromite", *base, "", "# --- fork", *ours]) + "\n"


def cmd_gn_args(args):
    sys.stdout.write(merged_gn_args(args.os, args.cpu))
    return EXIT_OK


# ---------------------------------------------------------------- privacy

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# reti private di casa e di Tailscale (100.64.0.0/10); le notazioni CIDR (10.0.0.0/8) vanno bene
PRIVATE_IP_RE = re.compile(r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168|"
                           r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))\.\d{1,3}\.\d{1,3}(?![\d./])")
SAFE_EMAIL_DOMAINS = ("users.noreply.github.com", "example.com", "example.org", "example.net")
SAFE_EMAILS = {"noreply@github.com", "noreply@anthropic.com"}
# parole vietate solo su questo computer (nome, indirizzi del NAS...): mai nel repo
DENYLIST = ROOT / ".privacy-denylist"


def privacy_problems():
    deny = []
    if DENYLIST.is_file():
        words = [w.strip() for w in DENYLIST.read_text().splitlines() if w.strip() and not w.startswith("#")]
        # parole intere: "ago" non deve scattare dentro "lago"
        deny = [re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.I) for w in words]
    own = patch_author()[1].lower()
    problems = []

    def safe_email(addr):
        addr = addr.lower()
        return addr == own or addr in SAFE_EMAILS or addr.split("@", 1)[1] in SAFE_EMAIL_DOMAINS

    def scan(where, line):
        if "privacy: ok" in line:
            return
        problems.extend(f"{where}: email {m.group(0)}" for m in EMAIL_RE.finditer(line)
                        if not safe_email(m.group(0)))
        problems.extend(f"{where}: indirizzo di rete privato {m.group(0)}" for m in PRIVATE_IP_RE.finditer(line))
        problems.extend(f"{where}: contiene una parola della lista locale" for w in deny if w.search(line))

    for rel in text(git("ls-files", "-z", cwd=ROOT)).split("\0"):
        path = ROOT / rel
        if not rel or not path.is_file() or path == DENYLIST:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        in_header = rel.endswith(".patch")
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if rel.endswith(".patch"):
                # nelle patch contano intestazione e righe aggiunte, non il contesto di Chromium
                in_header = in_header and not line.startswith("diff --git ")
                if not in_header and not (line.startswith("+") and not line.startswith("+++")):
                    continue
            scan(f"{rel}:{number}", line)

    log_format = "%h%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x01"
    for entry in text(git("log", f"--format={log_format}", cwd=ROOT, check=False)).split("\x01"):
        fields = entry.strip("\n").split("\0")
        if len(fields) < 6:
            continue
        sha, names, emails, message = fields[0], (fields[1], fields[3]), (fields[2], fields[4]), fields[5]
        for addr in emails:
            if not safe_email(addr):
                problems.append(f"commit {sha}: identità {addr} non anonima")
        for line in [*names, *message.splitlines()]:
            scan(f"commit {sha}", line)
    return problems


def cmd_privacy_check(args):
    problems = privacy_problems()
    if problems:
        raise ForkError("possibili dati personali nel repo:\n  " + "\n  ".join(problems) +
                        "\n(se una riga è voluta, aggiungi il commento `privacy: ok`)")
    extra = " e la lista locale" if DENYLIST.is_file() else ""
    log(f"OK: nessun dato personale trovato (file, commit{extra}).")
    return EXIT_OK


# ------------------------------------------------------------------- info

def cmd_latest(args):
    print(latest_tag())
    return EXIT_OK


def cmd_check(args):
    lock = read_lock()
    tag = args.to or latest_tag()
    update = tag != lock["cromite_tag"]
    m = TAG_RE.match(tag)
    if not m:
        raise ForkError(f"tag non valido: {tag}")
    log(f"attuale: {lock['cromite_tag']}\nupstream: {tag}\n" +
        ("aggiornamento disponibile" if update else "aggiornato"))
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"update={'true' if update else 'false'}\ntag={tag}\nversion={m.group(1)}\n")
    return EXIT_OK


def cmd_path(args):
    if args.what == "cromite":
        print(Cromite(read_lock()["cromite_tag"]).dir)
    return EXIT_OK


def cmd_status(args):
    lock = read_lock()
    series = read_series()
    print(f"Cromite: {lock['cromite_tag']} (Chromium {lock['chromium_version']})")
    print(f"Patch: {len(series)}")
    for rel in series:
        print(f"  {rel}")
    if state_file().is_file():
        state = json.loads(state_file().read_text())
        busy = state.get("mode") == "update" and not state.get("done")
        print(f"Sessione .work: {state.get('mode')}" + (" (in corso)" if busy else ""))
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(prog="forkctl", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="versione Cromite e patch attuali").set_defaults(fn=cmd_status)
    sub.add_parser("latest", help="ultima release stabile di Cromite").set_defaults(fn=cmd_latest)
    p = sub.add_parser("check", help="c'è un Cromite più nuovo?")
    p.add_argument("--to")
    p.add_argument("--github-output", action="store_true")
    p.set_defaults(fn=cmd_check)
    sub.add_parser("verify", help="le patch si applicano sulla versione bloccata?").set_defaults(fn=cmd_verify)
    sub.add_parser("privacy-check", help="cerca dati personali in file e commit").set_defaults(fn=cmd_privacy_check)
    p = sub.add_parser("update", help="riapplica le patch su un Cromite nuovo (rebase a 3 vie)")
    p.add_argument("--to", help="tag Cromite (default: ultima release stabile)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_update)
    p = sub.add_parser("continue", help="riprende l'aggiornamento dopo aver risolto i conflitti")
    p.add_argument("--drop-empty", action="store_true",
                   help="ritira la patch diventata vuota (modifica già presente upstream)")
    p.set_defaults(fn=cmd_continue)
    sub.add_parser("abort", help="annulla la sessione in .work").set_defaults(fn=cmd_abort)
    p = sub.add_parser("workspace", help="mini-albero in .work per scrivere o modificare patch")
    p.add_argument("--add", action="append", default=[], metavar="PATH",
                   help="includi anche questo file di Chromium (ripetibile)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_workspace)
    p = sub.add_parser("add", help="aggiunge file di Chromium al workspace aperto")
    p.add_argument("paths", nargs="+")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("export", help="scrive patches/ dai commit del workspace")
    p.add_argument("--allow-remove", action="store_true")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("apply-cromite", help="(host di build) applica Cromite a un checkout completo")
    p.add_argument("--src", required=True)
    p.set_defaults(fn=cmd_apply_cromite)
    p = sub.add_parser("apply", help="(host di build) applica le nostre patch e overlay/")
    p.add_argument("--src", required=True)
    p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("gn-args", help="args.gn finale per una piattaforma")
    p.add_argument("--os", required=True, choices=["win", "mac", "ios", "linux", "android"])
    p.add_argument("--cpu", required=True)
    p.set_defaults(fn=cmd_gn_args)
    p = sub.add_parser("path", help="percorsi utili per gli script")
    p.add_argument("what", choices=["cromite"])
    p.set_defaults(fn=cmd_path)
    args = parser.parse_args()
    try:
        return args.fn(args)
    except ForkError as e:
        log(f"errore: {e}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
