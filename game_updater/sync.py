"""Copying a downloaded version into a folder safely.

An update is worked out first as a Plan (what's new, changed and, when
mirroring, no longer in the repo), then applied with everything it replaces or
removes backed up, so it can be undone. If applying fails halfway (a file in
use, say), what was done is put back.

Unity keeps a .meta file next to every asset and folder. Repos often leave
them out, so when mirroring, a .meta file stays as long as the thing it
belongs to is still in the repo, and goes with it when it's removed.
"""

import fnmatch
import json
import os
import shutil
import stat
import time
import zipfile

KEEP_BACKUPS = 5  # per game
CHUNK = 1024 * 1024
# Never mirror into these: removing "extra" files there would be a disaster.
_RISKY = ("", "desktop", "documents", "downloads", "pictures", "videos", "music", "onedrive")


class SyncError(Exception):
    pass


# ----------------------------------------------------------------- unpacking
def unpack(archive, dest):
    """Extract a zip into dest and return the folder holding its contents.

    Zips often wrap everything in one top folder (GitHub's always do), so if
    there's just one folder and nothing else, that folder is returned.
    """
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():  # refuse paths that would land outside dest
            target = os.path.realpath(os.path.join(dest, member.filename))
            if not target.startswith(os.path.realpath(dest) + os.sep) and \
                    target != os.path.realpath(dest):
                raise SyncError(f"The download contains an unsafe path: {member.filename}")
        z.extractall(dest)
    root = dest
    while True:
        entries = [e for e in os.listdir(root) if e != "__MACOSX"]
        if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])):
            root = os.path.join(root, entries[0])
        else:
            return root


def source_folder(root, subfolder):
    """The folder inside the download to copy from (subfolder may be empty)."""
    sub = subfolder.strip().strip("/\\")
    path = os.path.normpath(os.path.join(root, sub)) if sub else root
    if not os.path.realpath(path).startswith(os.path.realpath(root)):
        raise SyncError("The folder in the repo must be inside it.")
    if not os.path.isdir(path):
        raise SyncError(f"The download has no folder “{sub}”. Check the folder in the repo.")
    return path


# -------------------------------------------------------------------- plans
def protected_patterns(text):
    """'*.cfg; saves/*' -> ['*.cfg', 'saves/*']"""
    return [p.strip().replace("\\", "/") for p in text.replace(",", ";").split(";") if p.strip()]


def _is_protected(rel, patterns):
    rel_l = rel.lower()
    name = rel_l.rsplit("/", 1)[-1]
    for p in patterns:
        p = p.lower().rstrip("/")
        if fnmatch.fnmatch(rel_l, p) or fnmatch.fnmatch(name, p) or rel_l.startswith(p + "/"):
            return True
    return False


def _walk(folder):
    """Relative paths ('a/b.txt') of every file under folder, and of every folder."""
    files, dirs = set(), set()
    for here, subdirs, names in os.walk(folder):
        rel_here = os.path.relpath(here, folder).replace(os.sep, "/")
        prefix = "" if rel_here == "." else rel_here + "/"
        dirs.update(prefix + d for d in subdirs)
        files.update(prefix + n for n in names)
    return files, dirs


def _same(a, b):
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(CHUNK), fb.read(CHUNK)
            if x != y:
                return False
            if not x:
                return True


class Plan:
    def __init__(self, source, target):
        self.source, self.target = source, target
        self.add, self.change, self.delete = [], [], []
        self.unchanged = 0
        self.target_files = 0  # how many files the target had

    @property
    def empty(self):
        return not (self.add or self.change or self.delete)

    def summary(self):
        parts = []
        for count, word in ((len(self.change), "changed"), (len(self.add), "new"),
                            (len(self.delete), "removed")):
            if count:
                parts.append(f"{count} {word}")
        return ", ".join(parts) if parts else "nothing to change"

    def drastic(self):
        """Mirroring would remove most of what's there: probably the wrong folder."""
        return len(self.delete) >= 10 and len(self.delete) > self.target_files * 0.5


def make_plan(source, target, mirror=False, protect=()):
    """Work out what copying source over target changes."""
    plan = Plan(source, target)
    src_files, src_dirs = _walk(source)
    dst_files, _ = _walk(target) if os.path.isdir(target) else (set(), set())
    plan.target_files = len(dst_files)
    src_lower = {p.lower() for p in src_files | src_dirs}
    dst_lower = {p.lower(): p for p in dst_files}
    for rel in sorted(src_files):
        if _is_protected(rel, protect):
            continue
        existing = dst_lower.get(rel.lower())
        if existing is None:
            plan.add.append(rel)
        elif _same(os.path.join(source, rel), os.path.join(target, existing)):
            plan.unchanged += 1
        else:
            plan.change.append(existing)
    if mirror:
        for rel in sorted(dst_files):
            low = rel.lower()
            if low in src_lower or _is_protected(rel, protect):
                continue
            if low.endswith(".meta") and low[:-5] in src_lower:
                continue  # Unity's file for something that's still there
            plan.delete.append(rel)
    return plan


def check_target(target, mirror):
    """Refuse folders an update must never touch. Returns an error message or None."""
    path = os.path.realpath(target)
    if not os.path.isabs(target) or not target.strip():
        return "Choose the folder to copy into."
    if os.path.dirname(path) == path:
        return "Copying into the top of a drive isn't allowed. Choose the game's folder."
    home = os.path.realpath(os.path.expanduser("~"))
    if mirror:
        for name in _RISKY:
            if path.lower() == os.path.join(home, name).rstrip(os.sep).lower():
                return ("Removing files that aren't in the repo would empty that folder. "
                        "Choose the game's own folder.")
    return None


# ------------------------------------------------------------ apply / undo
def _backup_root(data_dir, entry_id):
    return os.path.join(data_dir, "backups", entry_id)


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst) and not os.access(dst, os.W_OK):
        os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)  # read-only files can't be replaced
    shutil.copy2(src, dst)


def _remove(path):
    if not os.access(path, os.W_OK):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    os.remove(path)


def _prune_empty_dirs(target, rels):
    """Remove folders that removing files left empty (deepest first)."""
    dirs = set()
    for rel in rels:
        parts = rel.split("/")[:-1]
        for i in range(len(parts), 0, -1):
            dirs.add("/".join(parts[:i]))
    for rel in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
        path = os.path.join(target, rel)
        try:
            if os.path.isdir(path) and not os.listdir(path):
                os.rmdir(path)
        except OSError:
            pass


def apply(plan, data_dir, entry_id, info, on_progress=None):
    """Make the changes in plan, backing up what they replace. Returns the backup folder.

    info is kept with the backup (e.g. the version that was there before).
    On failure everything done so far is undone and SyncError is raised.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(_backup_root(data_dir, entry_id), stamp)
    files_dir = os.path.join(backup, "files")
    os.makedirs(files_dir, exist_ok=True)
    manifest = {"info": info, "time": time.time(), "target": plan.target,
                "added": [], "replaced": [], "deleted": []}
    steps = len(plan.change) + len(plan.add) + len(plan.delete)
    done = 0

    def save_manifest():
        with open(os.path.join(backup, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)

    def tick():
        nonlocal done
        done += 1
        if on_progress and steps:
            on_progress(done / steps)

    try:
        for rel in plan.change + plan.delete:  # keep copies of what's about to go
            _copy(os.path.join(plan.target, rel), os.path.join(files_dir, rel))
        for rel in plan.change:
            _copy(_source_path(plan, rel), os.path.join(plan.target, rel))
            manifest["replaced"].append(rel)
            tick()
        for rel in plan.add:
            _copy(os.path.join(plan.source, rel), os.path.join(plan.target, rel))
            manifest["added"].append(rel)
            tick()
        for rel in plan.delete:
            _remove(os.path.join(plan.target, rel))
            manifest["deleted"].append(rel)
            tick()
        _prune_empty_dirs(plan.target, plan.delete)
    except OSError as e:
        save_manifest()
        try:
            _restore(backup, manifest)
        finally:
            shutil.rmtree(backup, ignore_errors=True)
        name = os.path.basename(getattr(e, "filename", "") or "")
        hint = " Is the game (or Unity) still using it? Close it and try again." \
            if isinstance(e, PermissionError) else ""
        raise SyncError(f"Couldn't update {name or 'a file'}: {e.strerror or e}.{hint} "
                        "Nothing was changed.") from e
    save_manifest()
    _trim_backups(_backup_root(data_dir, entry_id))
    return backup


def _source_path(plan, rel):
    """The source file for a target path (whose letter case may differ)."""
    direct = os.path.join(plan.source, rel)
    if os.path.exists(direct):
        return direct
    folder = plan.source
    for part in rel.split("/"):
        match = next((n for n in os.listdir(folder) if n.lower() == part.lower()), part)
        folder = os.path.join(folder, match)
    return folder


def _restore(backup, manifest):
    target, files_dir = manifest["target"], os.path.join(backup, "files")
    for rel in manifest["added"]:
        try:
            _remove(os.path.join(target, rel))
        except FileNotFoundError:
            pass
    for rel in manifest["replaced"] + manifest["deleted"]:
        _copy(os.path.join(files_dir, rel), os.path.join(target, rel))
    _prune_empty_dirs(target, manifest["added"])


def _trim_backups(root):
    stamps = sorted(os.listdir(root)) if os.path.isdir(root) else []
    for old in stamps[:-KEEP_BACKUPS]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)


def last_backup(data_dir, entry_id):
    """(folder, manifest) of the newest update that can be undone, or None."""
    root = _backup_root(data_dir, entry_id)
    for stamp in sorted(os.listdir(root), reverse=True) if os.path.isdir(root) else []:
        try:
            with open(os.path.join(root, stamp, "manifest.json"), encoding="utf-8") as f:
                return os.path.join(root, stamp), json.load(f)
        except (OSError, ValueError):
            continue
    return None


def undo(data_dir, entry_id):
    """Put back the files from before the last update. Returns its manifest."""
    found = last_backup(data_dir, entry_id)
    if not found:
        raise SyncError("There's no update to undo.")
    backup, manifest = found
    try:
        _restore(backup, manifest)
    except OSError as e:
        raise SyncError(f"Couldn't undo: {e}. Close the game and try again.") from e
    shutil.rmtree(backup, ignore_errors=True)
    return manifest


def forget_backups(data_dir, entry_id):
    shutil.rmtree(_backup_root(data_dir, entry_id), ignore_errors=True)
