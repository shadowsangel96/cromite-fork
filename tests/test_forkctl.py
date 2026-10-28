#!/usr/bin/env python3
"""Test offline di forkctl: Chromium e Cromite finti, nessun accesso alla rete.

    python3 tests/test_forkctl.py
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
V1, V2 = "1.0.0.1", "1.0.0.2"
TAG1, TAG2 = f"v{V1}-{'a' * 40}", f"v{V2}-{'b' * 40}"
APPLY_SCRIPT = "#!/bin/bash\nrm -rf v8/.git\n"
FAKE_EMAIL = "mario.rossi@gmail.com"  # privacy: ok (dato finto per i test)
FAKE_LAN_IP = "192.168.1.20"  # privacy: ok (dato finto per i test)

BRANDING = "PRODUCT_FULLNAME=Chromium\nMAC_BUNDLE_ID=org.chromium.Chromium\n"
FEATURE_V1 = "".join(f"line {i}\n" for i in range(1, 31))
# v2: 5 righe nuove in cima (sposta tutto) e la riga 20 cambiata da upstream
FEATURE_V2 = "".join(f"new {i}\n" for i in range(1, 6)) + FEATURE_V1.replace("line 20\n", "line 20 upstream\n")


def run(*cmd, cwd, check=True, env=None):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check and res.returncode:
        raise AssertionError(f"{cmd} -> {res.returncode}\n{res.stdout}\n{res.stderr}")
    return res


def write(root, files):
    for path, content in files.items():
        f = Path(root) / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)


def make_patch(before, after, subject):
    """Patch in formato git format-patch che porta i file da `before` ad `after`."""
    with tempfile.TemporaryDirectory() as tmp:
        run("git", "init", "-q", cwd=tmp)
        run("git", "config", "user.name", "Test", cwd=tmp)
        run("git", "config", "user.email", "test@example.com", cwd=tmp)
        write(tmp, before)
        run("git", "add", "-A", cwd=tmp)
        run("git", "commit", "-qm", "base", cwd=tmp)
        write(tmp, after)
        run("git", "add", "-A", cwd=tmp)
        run("git", "commit", "-qm", subject, cwd=tmp)
        return run("git", "format-patch", "-1", "--stdout", cwd=tmp).stdout


class Fixture:
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.root = self.tmp / "repo"
        self.chromium = self.tmp / "chromium"
        self.cromite = self.tmp / "cromite"
        for d in ("tools", "patches", "state/rr-cache", "overlay", "gn_args", "reports"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "tools" / "forkctl.py", self.root / "tools")
        shutil.copy(REPO / "fork.json", self.root)
        (self.root / "patches" / "series").write_text("")
        self.set_lock(TAG1, V1)

        chromium = {V1: {"chrome/app/theme/chromium/BRANDING": BRANDING, "base/feature.cc": FEATURE_V1,
                         "other/y.txt": "y\n", "v8/include/v8.h": "// v8\n"},
                    V2: {"chrome/app/theme/chromium/BRANDING": BRANDING, "base/feature.cc": FEATURE_V2,
                         "other/y.txt": "y\n", "v8/include/v8.h": "// v8\n"}}
        for version, files in chromium.items():
            write(self.chromium / version, files)

        # Cromite tocca BRANDING e other/y.txt nella stessa patch (le nostre patch toccano
        # solo BRANDING: forkctl deve applicare solo quella parte) e un file del sottomodulo v8.
        brand = make_patch({"chrome/app/theme/chromium/BRANDING": BRANDING, "other/y.txt": "y\n"},
                           {"chrome/app/theme/chromium/BRANDING": BRANDING.replace("=Chromium\n", "=Cromite\n"),
                            "other/y.txt": "y cromite\n"}, "Cromite-Branding")
        v8 = make_patch({"v8/include/v8.h": "// v8\n"}, {"v8/include/v8.h": "// v8 cromite\n"}, "v8 tweak")
        for tag, version in ((TAG1, V1), (TAG2, V2)):
            base = self.cromite / tag
            write(base, {"build/RELEASE": version + "\n",
                         "build/cromite_patches_list.txt": "# commento\nCromite-Branding.patch\n\nv8-tweak.patch\n",
                         "build/patches/Cromite-Branding.patch": brand,
                         "build/patches/v8-tweak.patch": v8,
                         "build/cromite.gn_args": 'is_debug=false\ntreat_warnings_as_errors=true\n'
                                                  'if (target_os == "win") {\n  is_debug=true\n}\n',
                         "tools/images/cromite-source/apply-cromite-patches.sh": APPLY_SCRIPT})
        # niente identità git nell'ambiente: il workspace deve usare quella di fork.json
        self.env = {"FORKCTL_CHROMIUM_DIR": str(self.chromium), "FORKCTL_CROMITE_DIR": str(self.cromite),
                    "FORKCTL_LATEST_TAG": TAG2}
        # per i repo "upstream" finti costruiti dai test
        self.ident_env = {**self.env, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.com",
                          "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.com"}

    def set_lock(self, tag, version):
        (self.root / "upstream.lock").write_text(json.dumps({"cromite_tag": tag, "chromium_version": version}))

    def forkctl(self, *args, expect=0):
        res = run("python3", "tools/forkctl.py", *args, cwd=self.root, check=False, env=self.env)
        if res.returncode != expect:
            raise AssertionError(f"forkctl {args}: exit {res.returncode}, atteso {expect}\n{res.stderr}")
        return res

    def work(self, *args):
        return run("git", *args, cwd=self.root / ".work", env=self.env)

    def edit(self, path, old, new):
        f = self.root / ".work" / path
        f.write_text(f.read_text().replace(old, new, 1))

    def series(self):
        lines = (self.root / "patches" / "series").read_text().splitlines()
        return [line for line in lines if line and not line.startswith("#")]

    def lock(self):
        return json.loads((self.root / "upstream.lock").read_text())

    def add_patches(self, *changes):
        """Crea patch dal workspace: ogni change è (titolo, file, vecchio, nuovo)."""
        files = {c[1] for c in changes}
        self.forkctl("workspace", "--force", *[a for f in sorted(files) for a in ("--add", f)])
        for subject, path, old, new in changes:
            self.edit(path, old, new)
            self.work("commit", "-qam", subject)
        self.forkctl("export")


class ForkctlTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_workspace_applies_only_relevant_cromite_hunks(self):
        fx = self.fx
        fx.add_patches(("Branding fork", "chrome/app/theme/chromium/BRANDING", "=Cromite", "=Fork"))
        self.assertEqual(fx.series(), ["common/0001-branding-fork.patch"])
        patch = (fx.root / "patches" / fx.series()[0]).read_text()
        self.assertIn("Fork-Patch: common/0001-branding-fork.patch", patch)
        self.assertIn("-PRODUCT_FULLNAME=Cromite", patch)  # la base include già la patch Cromite
        self.assertFalse((fx.root / ".work" / "other" / "y.txt").exists())
        fx.forkctl("verify")

    def test_platform_prefix_chooses_folder(self):
        fx = self.fx
        fx.add_patches(("mac: bundle id", "chrome/app/theme/chromium/BRANDING", "org.chromium", "org.fork"))
        self.assertEqual(fx.series(), ["macos/0001-bundle-id.patch"])

    def test_update_clean_conflict_rerere_and_replay(self):
        fx = self.fx
        fx.add_patches(("A riga lontana", "base/feature.cc", "line 5\n", "line 5 fork\n"),
                       ("B riga cambiata upstream", "base/feature.cc", "line 20\n", "line 20 fork\n"))
        saved = {rel: (fx.root / "patches" / rel).read_text() for rel in fx.series()}

        fx.forkctl("update", expect=2)
        self.assertEqual(fx.lock()["cromite_tag"], TAG1)  # niente cambia finché non è risolto
        report = (fx.root / "reports" / f"update-{V2}.md").read_text()
        self.assertIn("**da risolvere**", report)
        fx.forkctl("continue", expect=1)  # marcatori ancora presenti

        f = fx.root / ".work" / "base" / "feature.cc"
        text = f.read_text()
        start, end = text.index("<<<<<<<"), text.index("\n", text.index(">>>>>>>")) + 1
        f.write_text(text[:start] + "line 20 upstream fork\n" + text[end:])
        fx.forkctl("continue")
        self.assertEqual(fx.lock()["cromite_tag"], TAG2)
        merged = fx.root / "patches" / "common" / "0002-b-riga-cambiata-upstream.patch"
        self.assertIn("+line 20 upstream fork", merged.read_text())
        self.assertIn("+line 5 fork", (fx.root / "patches" / fx.series()[0]).read_text())
        self.assertTrue(any((fx.root / "state" / "rr-cache").iterdir()))
        resolved = merged.read_text()

        # stesso aggiornamento rifatto da zero: rerere deve risolvere da solo
        for rel, content in saved.items():
            (fx.root / "patches" / rel).write_text(content)
        fx.set_lock(TAG1, V1)
        fx.forkctl("update")
        self.assertEqual(merged.read_text(), resolved)
        self.assertIn("risolto in automatico", (fx.root / "reports" / f"update-{V2}.md").read_text())
        fx.forkctl("verify")

    def test_empty_patch_needs_explicit_drop(self):
        fx = self.fx
        fx.add_patches(("Branding", "chrome/app/theme/chromium/BRANDING", "=Cromite", "=Fork"),
                       ("Come upstream", "base/feature.cc", "line 20\n", "line 20 upstream\n"))
        fx.forkctl("update", expect=3)
        fx.forkctl("continue", expect=1)
        fx.forkctl("continue", "--drop-empty")
        self.assertEqual(fx.series(), ["common/0001-branding.patch"])
        self.assertTrue((fx.root / "patches" / "retired" / "common" / "0002-come-upstream.patch").is_file())
        self.assertEqual(fx.lock()["cromite_tag"], TAG2)

    def test_export_refuses_to_lose_patches(self):
        fx = self.fx
        fx.add_patches(("Uno", "base/feature.cc", "line 1\n", "line 1 x\n"),
                       ("Due", "base/feature.cc", "line 2\n", "line 2 x\n"))
        fx.work("reset", "-q", "--hard", "HEAD~1")
        fx.forkctl("export", expect=1)
        self.assertEqual(len(fx.series()), 2)
        fx.forkctl("export", "--allow-remove")
        self.assertEqual(fx.series(), ["common/0001-uno.patch"])
        self.assertTrue((fx.root / "patches" / "retired" / "common" / "0002-due.patch").is_file())

    def test_workspace_guard_and_orphan_patch(self):
        fx = self.fx
        fx.add_patches(("Uno", "base/feature.cc", "line 1\n", "line 1 x\n"))
        fx.edit("base/feature.cc", "line 3\n", "line 3 non esportata\n")
        fx.work("commit", "-qam", "non esportata")
        fx.forkctl("workspace", expect=1)  # non butta via lavoro non esportato
        (fx.root / "patches" / "common" / "9999-orfana.patch").write_text("x")
        res = fx.forkctl("verify", expect=1)
        self.assertIn("non elencata in series", res.stderr)

    def test_add_file_to_open_workspace(self):
        fx = self.fx
        fx.add_patches(("Uno", "base/feature.cc", "line 1\n", "line 1 x\n"))
        fx.forkctl("add", "chrome/app/theme/chromium/BRANDING")
        fx.edit("chrome/app/theme/chromium/BRANDING", "=Cromite", "=Fork")
        fx.work("commit", "-qam", "Due")
        fx.forkctl("export")
        self.assertEqual(len(fx.series()), 2)
        fx.forkctl("verify")

    def test_gn_args_merge(self):
        fx = self.fx
        (fx.root / "gn_args" / "common.gn").write_text("is_debug=true\n")
        (fx.root / "gn_args" / "mac.gn").write_text("treat_warnings_as_errors = false\n")
        out = fx.forkctl("gn-args", "--os", "mac", "--cpu", "arm64").stdout
        self.assertIn('target_os = "mac"', out)
        self.assertIn("# (sovrascritto dal fork) is_debug=false", out)
        self.assertIn("# (sovrascritto dal fork) treat_warnings_as_errors=true", out)
        self.assertIn("  is_debug=true", out)  # dentro un blocco if: non toccato
        (fx.root / "gn_args" / "ios.gn").write_text("# forkctl: senza-args-cromite\nis_debug=false\n")
        self.assertNotIn("treat_warnings_as_errors", fx.forkctl("gn-args", "--os", "ios", "--cpu", "arm64").stdout)

    def test_full_tree_apply_cromite_then_fork(self):
        fx = self.fx
        fx.add_patches(("Branding", "chrome/app/theme/chromium/BRANDING", "=Cromite", "=Fork"),
                       ("v8 nostro", "v8/include/v8.h", "// v8 cromite\n", "// v8 fork\n"))
        fx.forkctl("update")
        write(fx.root / "overlay", {"chrome/app/theme/fork.png": "PNG"})

        # checkout "completo" finto: v8 è un sottomodulo, come in Chromium
        v8 = fx.tmp / "v8-upstream"
        write(v8, {"include/v8.h": "// v8\n"})
        for cmd in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "v8")):
            run("git", *cmd, cwd=v8, env=fx.ident_env)
        src = fx.tmp / "full" / "src"
        write(src, {k: v for k, v in {"chrome/app/theme/chromium/BRANDING": BRANDING, "base/feature.cc": FEATURE_V2,
                                       "other/y.txt": "y\n",
                                       "chrome/VERSION": "MAJOR=1\nMINOR=0\nBUILD=0\nPATCH=2\n"}.items()})
        run("git", "init", "-q", cwd=src)
        run("git", "add", "-A", cwd=src)
        run("git", "commit", "-qm", "chromium", cwd=src, env=fx.ident_env)
        run("git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(v8), "v8", cwd=src, env=fx.ident_env)
        run("git", "commit", "-qm", "v8 submodule", cwd=src, env=fx.ident_env)

        fx.forkctl("apply-cromite", "--src", str(src))
        fx.forkctl("apply", "--src", str(src))
        self.assertIn("PRODUCT_FULLNAME=Fork", (src / "chrome/app/theme/chromium/BRANDING").read_text())
        self.assertEqual((src / "other/y.txt").read_text(), "y cromite\n")
        self.assertEqual((src / "v8/include/v8.h").read_text(), "// v8 fork\n")
        self.assertEqual((src / "chrome/app/theme/fork.png").read_text(), "PNG")
        log = run("git", "log", "--format=%s", cwd=src).stdout.split("\n")
        self.assertEqual(log[:6], ["fork: overlay", "v8 nostro", "Branding", "v8 tweak", "Cromite-Branding",
                                   ":NOEXPORT: v8 repo"])
        self.assertEqual(run("git", "status", "--porcelain", cwd=src).stdout, "")

        fx.set_lock(TAG1, V1)
        fx.forkctl("apply", "--src", str(src), expect=1)  # versione sbagliata: si rifiuta


    def test_patches_use_public_author(self):
        fx = self.fx
        fx.add_patches(("Uno", "base/feature.cc", "line 1\n", "line 1 x\n"))
        patch = fx.root / "patches" / fx.series()[0]
        author = json.loads((fx.root / "fork.json").read_text())["patch_author"]
        self.assertIn(f"From: {author['name']} <{author['email']}>", patch.read_text())
        patch.write_text(patch.read_text().replace(author["email"], FAKE_EMAIL))
        self.assertIn("autore diverso", fx.forkctl("verify", expect=1).stderr)

    def test_privacy_check(self):
        fx = self.fx
        author = json.loads((fx.root / "fork.json").read_text())["patch_author"]
        ident = ["-c", f"user.name={author['name']}", "-c", f"user.email={author['email']}"]

        def commit(files, message="modifica"):
            write(fx.root, files)
            run("git", "add", "-A", cwd=fx.root)
            run("git", *ident, "commit", "-qm", message, cwd=fx.root)

        run("git", "init", "-q", cwd=fx.root)
        commit({"note.md": "rete 10.0.0.0/8, versione SDK 10.0.26100.0, test@example.com\n"})
        fx.forkctl("privacy-check")
        commit({"note.md": f"il NAS è su {FAKE_LAN_IP}\n"})
        self.assertIn(FAKE_LAN_IP, fx.forkctl("privacy-check", expect=1).stderr)
        commit({"note.md": "tailscale 100.101.102.103 # privacy: ok\n"})
        fx.forkctl("privacy-check")
        commit({"note.md": f"scrivimi a {FAKE_EMAIL}\n"})
        self.assertIn(FAKE_EMAIL, fx.forkctl("privacy-check", expect=1).stderr)
        commit({"note.md": "pulito\n"}, f"contatto: {FAKE_EMAIL}")
        self.assertIn("commit", fx.forkctl("privacy-check", expect=1).stderr)

    def test_privacy_denylist_is_local_and_whole_word(self):
        fx = self.fx
        author = json.loads((fx.root / "fork.json").read_text())["patch_author"]
        run("git", "init", "-q", cwd=fx.root)
        write(fx.root, {"note.md": "Segretissimo va bene\n", ".gitignore": "/.privacy-denylist\n",
                        ".privacy-denylist": "# locale\nSegreto\n"})
        run("git", "add", "-A", cwd=fx.root)
        run("git", "-c", f"user.name={author['name']}", "-c", f"user.email={author['email']}",
            "commit", "-qm", "base", cwd=fx.root)
        self.assertNotIn(".privacy-denylist", run("git", "ls-files", cwd=fx.root).stdout)
        fx.forkctl("privacy-check")
        write(fx.root, {"note.md": "il Segreto è qui\n"})
        self.assertIn("lista locale", fx.forkctl("privacy-check", expect=1).stderr)

if __name__ == "__main__":
    unittest.main(verbosity=2)
