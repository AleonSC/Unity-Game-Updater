"""`GameUpdater.exe --selftest report.txt` checks the packaged app works.

Exit code 0 if every check passed. Used by the build workflow.
"""

import os
import shutil
import tempfile
import traceback
import zipfile
from unittest import mock

from . import github
from . import sync
from .version import __version__


def _write(root, files):
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def _read(root):
    out = {}
    for here, _, names in os.walk(root):
        for n in names:
            path = os.path.join(here, n)
            with open(path, encoding="utf-8") as f:
                out[os.path.relpath(path, root).replace(os.sep, "/")] = f.read()
    return out


def run(report_path):
    results = []
    ok = True
    work = tempfile.mkdtemp(prefix="gameupdater-selftest-")

    def check(name, fn):
        nonlocal ok
        try:
            results.append(f"ok   {name}: {fn()}")
        except Exception:
            ok = False
            results.append(f"FAIL {name}: {traceback.format_exc()}")

    def links():
        cases = {
            "AleonSC/PixelClicker": ("AleonSC/PixelClicker", ""),
            "https://github.com/AleonSC/PixelClicker.git": ("AleonSC/PixelClicker", ""),
            "https://github.com/AleonSC/PixelClicker/tree/claude/x-y/Assets/Scripts":
                ("AleonSC/PixelClicker", "claude/x-y/Assets/Scripts"),
        }
        for text, want in cases.items():
            assert github.parse_repo(text) == want, (text, github.parse_repo(text))
        split = github.split_tree_path("claude/x-y/Assets/Scripts", ["main", "claude/x-y"])
        assert split == ("claude/x-y", "Assets/Scripts"), split
        return "links and owner/name understood"

    def unity_mirror():
        repo, game, data = (os.path.join(work, d) for d in ("repo", "game", "data"))
        _write(repo, {"Assets/Scripts/A.cs": "a2", "Assets/Scripts/New.cs": "new",
                      "Assets/Scripts/Sub/C.cs": "c", "README.md": "readme"})
        _write(game, {"A.cs": "a1", "A.cs.meta": "ma", "Old.cs": "old", "Old.cs.meta": "mo",
                      "Sub/C.cs": "c", "Sub.meta": "msub", "Gone/D.cs": "d", "Gone.meta": "mg",
                      "settings.cfg": "mine"})
        archive = os.path.join(work, "repo.zip")
        with zipfile.ZipFile(archive, "w") as z:  # GitHub-style: one top folder
            for rel in _read(repo):
                z.write(os.path.join(repo, rel), "Repo-abc123/" + rel)
        root = sync.unpack(archive, os.path.join(work, "unpacked"))
        source = sync.source_folder(root, "Assets/Scripts")
        plan = sync.make_plan(source, game, mirror=True, protect=sync.protected_patterns("*.cfg"))
        assert plan.change == ["A.cs"] and plan.add == ["New.cs"], (plan.change, plan.add)
        assert sorted(plan.delete) == ["Gone.meta", "Gone/D.cs", "Old.cs", "Old.cs.meta"], plan.delete
        sync.apply(plan, data, "t1", {"installed": None})
        after = _read(game)
        assert after == {"A.cs": "a2", "A.cs.meta": "ma", "New.cs": "new", "Sub/C.cs": "c",
                         "Sub.meta": "msub", "settings.cfg": "mine"}, after
        assert not os.path.exists(os.path.join(game, "Gone")), "empty folder left"
        sync.undo(data, "t1")
        assert _read(game)["Old.cs"] == "old" and _read(game)["A.cs"] == "a1"
        assert "New.cs" not in _read(game)
        return "copied, kept .meta files and protected files, removed stale ones, undid it"

    def rollback():
        src, dst, data = (os.path.join(work, d) for d in ("src2", "dst2", "data2"))
        _write(src, {"a.txt": "new a", "b.txt": "new b", "c.txt": "new c"})
        _write(dst, {"a.txt": "old a", "b.txt": "old b", "c.txt": "old c"})
        plan = sync.make_plan(src, dst)
        real = shutil.copy2
        calls = {"n": 0}

        def flaky(a, b, *k, **kw):
            if os.path.dirname(os.path.abspath(b)) == os.path.abspath(dst):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise PermissionError(13, "The file is in use", b)
            return real(a, b, *k, **kw)

        with mock.patch("shutil.copy2", flaky):
            try:
                sync.apply(plan, data, "t2", {})
                raise AssertionError("should have failed")
            except sync.SyncError as e:
                message = str(e)
        assert _read(dst) == {"a.txt": "old a", "b.txt": "old b", "c.txt": "old c"}, _read(dst)
        return f"a failed update was put back ({message[:60]}...)"

    def guards():
        assert sync.check_target(os.path.abspath(os.sep), False)
        assert sync.check_target(os.path.join(os.path.expanduser("~"), "Desktop"), True)
        assert sync.check_target(os.path.join(work, "game"), True) is None
        src, dst = os.path.join(work, "src3"), os.path.join(work, "dst3")
        _write(src, {"one.txt": "1"})
        _write(dst, {f"f{i}.txt": "x" for i in range(30)})
        assert sync.make_plan(src, dst, mirror=True).drastic()
        return "refuses drive tops and personal folders; warns before emptying a folder"

    def unity_checks():
        project = os.path.join(work, "MyGame")
        _write(project, {"ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 6000.0",
                         "Assets/Scripts/Clicker.cs": "new", "Assets/Old/Clicker.cs": "old",
                         "Assets/Scenes/Main.unity": "scene"})
        scripts = os.path.join(project, "Assets", "Scripts")
        assert sync.unity_project(scripts) == project
        assert sync.unity_target(project, "Assets/Scripts") == scripts
        assert sync.unity_target(os.path.join(project, "Assets"), "Assets/Scripts") == scripts
        assert sync.unity_target(scripts, "Assets/Scripts") is None
        dupes = sync.duplicate_scripts(scripts)
        assert dupes == [os.path.join("Assets", "Old", "Clicker.cs")], dupes
        build = os.path.join(work, "Build")
        _write(build, {"UnityPlayer.dll": "", "Game_Data/x": ""})
        assert sync.built_game(os.path.join(build, "Game_Data"))
        assert not sync.built_game(scripts)
        return "spots the wrong folder in a project, old script copies, and built games"

    def live_github():
        hub = github.GitHub()
        head = hub.branch_head("AleonSC/unity-game-updater", "main")
        archive = os.path.join(work, "live.zip")
        assert hub.download(head["url"], archive)
        root = sync.unpack(archive, os.path.join(work, "live"))
        assert os.path.exists(os.path.join(root, "game_updater", "sync.py")), os.listdir(root)
        return f"downloaded main at {head['version'][:7]} ({os.path.getsize(archive)} bytes)"

    def dark_mode():
        import tkinter as tk
        from . import theme

        root = tk.Tk()
        try:
            theme.apply(root, True)
            theme.apply(root, False)
        finally:
            root.destroy()
        return "switches both ways"

    check("version", lambda: __version__)
    check("GitHub links", links)
    check("Unity folder sync", unity_mirror)
    check("failed update rolled back", rollback)
    check("safety checks", guards)
    check("Unity checks", unity_checks)
    check("download from GitHub", live_github)
    check("dark mode", dark_mode)
    shutil.rmtree(work, ignore_errors=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(results) + "\n")
    return 0 if ok else 1
