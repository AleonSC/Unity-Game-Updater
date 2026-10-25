"""Game Updater: keep game folders up to date from GitHub with one click."""

import concurrent.futures
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import uuid
import webbrowser
import zipfile
from tkinter import messagebox, ttk

from . import github
from . import self_update
from . import sync
from . import theme
from .entry_dialog import EntryDialog
from .tooltip import Tooltip
from .version import __version__

COLUMNS = (("from", "From", 300), ("installed", "Installed", 150),
           ("latest", "Latest", 150), ("status", "Status", 170))


# ---------------------------------------------------------------- config
def _config_path():
    return os.path.join(theme.data_dir(), "games.json")


def load_entries():
    try:
        with open(_config_path(), encoding="utf-8") as f:
            entries = json.load(f)["games"]
        return [e for e in entries if isinstance(e, dict) and e.get("id") and e.get("repo")]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_entries(entries):
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    keep = [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"games": keep}, f, indent=1)
    os.replace(path + ".tmp", path)


# ---------------------------------------------------------------- words
def ago(iso):
    """'2026-10-25T16:37:30Z' -> '5 min ago'."""
    try:
        when = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return ""
    seconds = (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds()
    for size, unit in ((86400 * 365, "year"), (86400 * 30, "month"), (86400, "day"),
                       (3600, "hour"), (60, "min")):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'s' if n > 1 and unit != 'min' else ''} ago"
    return "just now"


def version_text(found, mode):
    if not found:
        return ""
    version = found["version"][:7] if mode == "branch" else found["version"]
    when = ago(found.get("date"))
    return f"{version}, {when}" if when else version


def first_line(text):
    return (text or "").strip().splitlines()[0][:160] if (text or "").strip() else ""


class GameUpdaterApp:
    def __init__(self, root):
        self.root = root
        root.title("Game Updater" if __version__ == "dev" else f"Game Updater {__version__}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self.icon = tk.PhotoImage(file=asset("icon.png"))
            root.iconphoto(True, self.icon)
        except tk.TclError:
            pass
        self.entries = load_entries()
        self.busy = False
        self.cancel_requested = False
        self.queue = []  # entries waiting for "Update all"
        self.dark_mode = tk.BooleanVar(value=theme.dark)
        self.ask = tk.BooleanVar(value=bool(theme.load_setting("ask", True)))
        self.ask.trace_add("write", lambda *_: theme.save_setting("ask", self.ask.get()))
        self.status = tk.StringVar()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.new_release = None
        self._build()
        self._refresh_list()
        self.check_all()
        if self_update.can_update():
            threading.Thread(target=self._check_self_update, daemon=True).start()

    # ------------------------------------------------------------- basics
    def github(self):
        return github.GitHub(theme.load_setting("token", ""))

    def from_thread(self, fn):
        try:
            self.root.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass

    def _entry(self, entry_id):
        return next((e for e in self.entries if e["id"] == entry_id), None)

    def selected(self):
        sel = self.tree.selection()
        return self._entry(sel[0]) if sel else None

    # ----------------------------------------------------------------- UI
    def _build(self):
        main = ttk.Frame(self.root, padding=(10, 8, 10, 6))
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x")
        for text, command, tip in (
                ("Add...", self.add, "Add a game (or any folder) to keep up to date from GitHub"),
                ("Change...", self.edit, "Change where the selected game comes from or goes"),
                ("Remove", self.remove, "Take it off the list (its files aren't touched)")):
            button = ttk.Button(top, text=text, command=command)
            button.pack(side="left", padx=(0, 4))
            Tooltip(button, tip)
        ttk.Checkbutton(top, text="Dark", variable=self.dark_mode,
                        command=self._toggle_dark).pack(side="right")
        ttk.Separator(top, orient="vertical").pack(side="right", fill="y", padx=10, pady=3)
        settings = ttk.Button(top, text="Settings...", command=self.settings)
        settings.pack(side="right")
        Tooltip(settings, "A GitHub token for private repos (optional)")
        ask = ttk.Checkbutton(top, text="Show changes first", variable=self.ask)
        ask.pack(side="right", padx=(0, 12))
        Tooltip(ask, "List what an update will change and ask before doing it. (Updates that "
                     "would remove most of a folder always ask.)")
        self.update_all_btn = ttk.Button(top, text="Update all", command=self.update_all)
        self.update_all_btn.pack(side="right", padx=(0, 12))
        check = ttk.Button(top, text="Check", command=self.check_all)
        check.pack(side="right", padx=(0, 4))
        Tooltip(check, "Look for updates now (it also does when it starts)")

        lst = ttk.Frame(main)
        lst.pack(fill="both", expand=True, pady=(8, 6))
        self.tree = ttk.Treeview(lst, columns=[c[0] for c in COLUMNS], selectmode="browse",
                                 height=8)
        self.tree.heading("#0", text="Game", anchor="w")
        self.tree.column("#0", width=170, minwidth=100)
        for key, title, width in COLUMNS:
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=width, minwidth=80)
        scroll = ttk.Scrollbar(lst, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_details())
        self.tree.bind("<Double-Button-1>", lambda e: self.edit())
        self.tree.bind("<Return>", lambda e: self.update_selected())
        self.tree.bind("<Delete>", lambda e: self.remove())
        self.empty_note = ttk.Label(lst, style="Muted.TLabel", text=(
            "Nothing here yet. Click Add... and paste a GitHub repo's link."))

        detail = ttk.Frame(main)
        detail.pack(fill="x")
        self.detail = ttk.Label(detail, style="Muted.TLabel", width=1, justify="left")
        self.detail.pack(side="left", fill="x", expand=True)
        self.update_btn = ttk.Button(detail, text="Update", command=self.update_selected)
        self.update_btn.pack(side="right")
        Tooltip(self.update_btn, "Get the latest and copy it into the folder  (Enter)")
        self.undo_btn = ttk.Button(detail, text="Undo last update", command=self.undo_selected)
        self.undo_btn.pack(side="right", padx=4)
        Tooltip(self.undo_btn, "Put the files back the way they were before the last update")
        for text, command in (("Open on GitHub", self.open_github), ("Open folder", self.open_folder)):
            ttk.Button(detail, text=text, command=command).pack(side="right", padx=(0, 4))

        bar = ttk.Frame(main)
        bar.pack(fill="x", pady=(8, 0))
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.cancel)
        self.progress = ttk.Progressbar(bar, maximum=1.0, length=180)
        self.self_update_btn = ttk.Button(bar, command=self.install_self_update)
        ttk.Label(bar, textvariable=self.status, style="Muted.TLabel", width=1).pack(
            side="left", fill="x", expand=True)

        self.root.bind("<F5>", lambda e: self.check_all())
        self.root.update_idletasks()
        self.root.minsize(760, 320)

    def _toggle_dark(self):
        theme.apply(self.root, self.dark_mode.get())
        theme.save_dark_setting(self.dark_mode.get())

    def _row_values(self, e):
        where = f"{e['repo']} · " + (e["branch"] if e["mode"] == "branch" else "latest release")
        if e.get("subfolder"):
            where += f" · {e['subfolder']}"
        latest = e.get("_latest")
        if e.get("_state"):
            status = e["_state"]
        elif e.get("_error"):
            status = "⚠ " + e["_error"]
        elif latest is None:
            status = ""
        elif not e.get("installed"):
            status = "Not installed yet"
        elif e["installed"]["version"] == latest["version"]:
            status = "✓ Up to date"
        else:
            status = "Update available"
        return (where, version_text(e.get("installed"), e["mode"]),
                version_text(latest, e["mode"]), status)

    def _refresh_list(self):
        current = set(self.tree.get_children())
        wanted = [e["id"] for e in self.entries]
        for iid in current - set(wanted):
            self.tree.delete(iid)
        for i, e in enumerate(self.entries):
            if e["id"] in current:
                self.tree.item(e["id"], text=e["name"], values=self._row_values(e))
                self.tree.move(e["id"], "", i)
            else:
                self.tree.insert("", i, iid=e["id"], text=e["name"], values=self._row_values(e))
        if self.entries:
            self.empty_note.place_forget()
            if not self.tree.selection():
                self.tree.selection_set(self.entries[0]["id"])
        else:
            self.empty_note.place(relx=0.5, rely=0.5, anchor="center")
        self._update_details()

    def _update_details(self):
        e = self.selected()
        state = "normal" if e and not self.busy else "disabled"
        self.update_btn.config(state=state)
        can_undo = bool(e) and not self.busy and sync.last_backup(theme.data_dir(), e["id"])
        self.undo_btn.config(state="normal" if can_undo else "disabled")
        self.update_all_btn.config(state="disabled" if self.busy or not self.entries else "normal")
        if not e:
            self.detail.config(text="")
            return
        latest = e.get("_latest")
        text = f"Into {e['target']}"
        if latest:
            text = f"Latest: {first_line(latest.get('message'))}   →   " + text
        self.detail.config(text=text)

    # -------------------------------------------------------------- list
    def add(self):
        EntryDialog(self, {"id": uuid.uuid4().hex[:10], "mode": "branch", "mirror": False},
                    self._saved)

    def edit(self):
        e = self.selected()
        if e and not self.busy:
            EntryDialog(self, e, self._saved)

    def _saved(self, entry):
        old = self._entry(entry["id"])
        if old is None:
            self.entries.append(entry)
        else:
            self.entries[self.entries.index(old)] = entry
        if entry.get("installed") is None:
            sync.forget_backups(theme.data_dir(), entry["id"])
        self._save()
        self._refresh_list()
        self.tree.selection_set(entry["id"])
        self.check(entry)

    def remove(self):
        e = self.selected()
        if not e or self.busy:
            return
        if not messagebox.askyesno("Remove", f"Take “{e['name']}” off the list? Its files "
                                             "stay where they are."):
            return
        self.entries.remove(e)
        sync.forget_backups(theme.data_dir(), e["id"])
        self._save()
        self.tree.selection_set(())
        self._refresh_list()

    def _save(self):
        try:
            save_entries(self.entries)
        except OSError as err:
            messagebox.showerror("Game Updater", f"Couldn't save the list: {err}")

    def open_folder(self):
        e = self.selected()
        if e and os.path.isdir(e["target"]):
            open_path(e["target"])

    def open_github(self):
        e = self.selected()
        if e:
            url = f"https://github.com/{e['repo']}"
            if e["mode"] == "branch":
                url += f"/tree/{e['branch']}" + (f"/{e['subfolder']}" if e.get("subfolder") else "")
            else:
                url += "/releases/latest"
            webbrowser.open(url)

    def settings(self):
        SettingsDialog(self)

    # ------------------------------------------------------------- check
    def check_all(self):
        for e in self.entries:
            self.check(e)

    def check(self, e, then=None):
        """Find the latest version of e in the background; then(found or None) after."""
        e["_state"], e["_error"] = "Checking...", None
        self._refresh_list()
        hub = self.github()

        def work():
            try:
                if e["mode"] == "branch":
                    found = hub.branch_head(e["repo"], e["branch"])
                else:
                    found = hub.latest_release(e["repo"], e.get("asset", ""))
                error = None
            except github.GitHubError as err:
                found, error = None, str(err)
            self.from_thread(lambda: self._checked(e, found, error, then))

        self.pool.submit(work)

    def _checked(self, e, found, error, then):
        e["_state"] = None
        e["_error"] = error
        if found:
            e["_latest"] = found
        self._refresh_list()
        if then:
            then(found)

    # ------------------------------------------------------------ update
    def update_selected(self):
        e = self.selected()
        if e and not self.busy:
            self.queue = [e]
            self._next_in_queue()

    def update_all(self):
        if self.busy or not self.entries:
            return
        self.queue = list(self.entries)
        self._next_in_queue()

    def _next_in_queue(self):
        while self.queue:
            e = self.queue.pop(0)
            if self._entry(e["id"]) is not None:
                self._set_busy(True)
                self.check(e, lambda found, e=e: self._got_latest(e, found))
                return
        self._set_busy(False)

    def _set_busy(self, busy):
        self.busy = busy
        self.cancel_requested = False
        if busy:
            self.cancel_btn.pack(side="right")
            self.progress.pack(side="right", padx=6)
        else:
            self.cancel_btn.pack_forget()
            self.progress.pack_forget()
        self.progress.config(value=0)
        self._update_details()

    def cancel(self):
        self.cancel_requested = True
        self.queue = []
        self.status.set("Cancelling...")

    def _got_latest(self, e, found):
        if found is None or self.cancel_requested:
            self._next_in_queue()
            return
        e["_state"] = "Downloading..."
        self._refresh_list()
        self.status.set(f"{e['name']}: downloading...")
        hub = self.github()

        def progress(done, total):
            text = f"{e['name']}: downloading... {done / 1048576:.1f} MB"
            self.from_thread(lambda: (self.status.set(text),
                                      self.progress.config(value=done / total if total else 0)))

        def work():
            temp = tempfile.mkdtemp(prefix="gameupdater-")
            try:
                name = found.get("file", "download.zip")
                archive = os.path.join(temp, name)
                if not hub.download(found["url"], archive, progress, lambda: self.cancel_requested):
                    raise sync.SyncError("Cancelled.")
                self.from_thread(lambda: self.status.set(f"{e['name']}: comparing files..."))
                if zipfile.is_zipfile(archive):
                    root = sync.unpack(archive, os.path.join(temp, "unpacked"))
                else:  # a single file, e.g. a game's exe: copy it as it is
                    root = os.path.join(temp, "single")
                    os.makedirs(root)
                    shutil.move(archive, os.path.join(root, name))
                source = sync.source_folder(root, e.get("subfolder", ""))
                plan = sync.make_plan(source, e["target"], e.get("mirror", False),
                                      sync.protected_patterns(e.get("protect", "")))
                self.from_thread(lambda: self._planned(e, found, plan, temp))
            except (github.GitHubError, sync.SyncError, OSError, zipfile.BadZipFile) as err:
                shutil.rmtree(temp, ignore_errors=True)
                self.from_thread(lambda err=err: self._update_failed(e, err))

        threading.Thread(target=work, daemon=True).start()

    def _planned(self, e, found, plan, temp):
        e["_state"] = None
        if self.cancel_requested:
            shutil.rmtree(temp, ignore_errors=True)
            self.status.set("Cancelled.")
            self._next_in_queue()
            return
        if plan.empty:
            shutil.rmtree(temp, ignore_errors=True)
            self._installed(e, found)
            self.status.set(f"{e['name']}: already up to date.")
            self._check_unity(e)
            self._next_in_queue()
            return
        if self.ask.get() or plan.drastic():
            ChangesDialog(self, e, found, plan, lambda ok: self._confirmed(ok, e, found, plan, temp))
        else:
            self._confirmed(True, e, found, plan, temp)

    def _confirmed(self, ok, e, found, plan, temp):
        if not ok:
            shutil.rmtree(temp, ignore_errors=True)
            self.status.set(f"{e['name']}: not updated.")
            self._refresh_list()
            self._next_in_queue()
            return
        e["_state"] = "Updating..."
        self._refresh_list()
        info = {"installed": e.get("installed")}

        def progress(frac):
            self.from_thread(lambda: self.progress.config(value=frac))

        def work():
            try:
                os.makedirs(e["target"], exist_ok=True)
                sync.apply(plan, theme.data_dir(), e["id"], info, progress)
                error = None
            except (sync.SyncError, OSError) as err:
                error = err
            finally:
                shutil.rmtree(temp, ignore_errors=True)
            self.from_thread(lambda: self._applied(e, found, plan, error))

        threading.Thread(target=work, daemon=True).start()

    def _applied(self, e, found, plan, error):
        e["_state"] = None
        if error:
            self._update_failed(e, error)
            return
        self._installed(e, found)
        self.status.set(f"{e['name']}: updated ({plan.summary()})."
                        + (" Switch to Unity to load the changes." if sync.unity_project(
                            e["target"]) else ""))
        self._check_unity(e)
        self._next_in_queue()

    def _check_unity(self, e):
        """Warn about what stops Unity using the update: a folder outside the
        project's scripts, or old copies of the scripts elsewhere in the project."""
        target = e["target"]
        better = sync.unity_target(target, e.get("subfolder", ""))
        if better:
            messagebox.showwarning(
                "Unity won't see this update",
                f"{e['name']} is copied into {target}, but in a Unity project the files "
                f"belong in\n{better}\n\nClick Undo last update to take the misplaced "
                "copies out again, then Change... and pick that folder.")
            return
        dupes = sync.duplicate_scripts(target) if sync.unity_project(target) else []
        if dupes:
            shown = "\n".join(dupes[:15]) + (f"\n...and {len(dupes) - 15} more"
                                               if len(dupes) > 15 else "")
            messagebox.showwarning(
                "Old copies of the scripts",
                f"The project has other copies of {len(dupes)} of these scripts outside "
                f"{target}:\n\n{shown}\n\nWith two copies Unity can't compile, so the game "
                "keeps running the old code. Delete those old copies in Unity's Project window "
                "(after checking they're not something you changed yourself).")

    def _installed(self, e, found):
        e["installed"] = {k: found[k] for k in ("version", "date", "message")}
        e["_error"] = None
        self._save()
        self._refresh_list()

    def _update_failed(self, e, error):
        e["_state"] = None
        if str(error) != "Cancelled.":
            e["_error"] = str(error)
        self.status.set(f"{e['name']}: {error}")
        self._refresh_list()
        self._next_in_queue()

    def undo_selected(self):
        e = self.selected()
        if not e or self.busy:
            return
        found = sync.last_backup(theme.data_dir(), e["id"])
        if not found:
            return
        _, manifest = found
        counts = [f"{len(manifest[k])} {w}" for k, w in (
            ("replaced", "changed back"), ("added", "new removed"), ("deleted", "put back"))
            if manifest[k]]
        if not messagebox.askyesno("Undo last update",
                                   f"Put “{e['name']}” back the way it was before its last "
                                   f"update? ({', '.join(counts)})"):
            return
        try:
            manifest = sync.undo(theme.data_dir(), e["id"])
        except sync.SyncError as err:
            messagebox.showerror("Undo last update", str(err))
            return
        e["installed"] = (manifest.get("info") or {}).get("installed")
        self._save()
        self._refresh_list()
        self.status.set(f"{e['name']}: last update undone.")

    # ------------------------------------------------------- self update
    def _check_self_update(self):
        try:
            found = self_update.check()
        except (OSError, ValueError, KeyError):
            return
        if found:
            self.from_thread(lambda: self._self_update_found(found))

    def _self_update_found(self, found):
        self.new_release = found
        self.self_update_btn.config(text=f"Update Game Updater to {found['version']}")
        self.self_update_btn.pack(side="right", padx=(10, 0))

    def install_self_update(self):
        found = self.new_release
        if not self_update.is_installed() or not found["setup_url"]:
            webbrowser.open(found["page"])
            return
        if self.busy:
            return
        self.self_update_btn.config(state="disabled")
        self.status.set("Downloading the new Game Updater...")

        def work():
            try:
                path = self_update.download(found)
                self.from_thread(lambda: (self_update.install(path), self.close()))
            except OSError as err:
                self.from_thread(lambda err=err: (self.status.set(f"Couldn't download it: {err}"),
                                          self.self_update_btn.config(state="normal")))

        threading.Thread(target=work, daemon=True).start()

    def close(self):
        self.cancel_requested = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


class ChangesDialog(tk.Toplevel):
    """Lists what an update will change; on_done(True) to go ahead."""

    def __init__(self, app, e, found, plan, on_done):
        super().__init__(app.root)
        self.on_done, self.answered = on_done, False
        self.title(f"Update {e['name']}")
        self.transient(app.root)
        theme.style_window(self)
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)
        version = version_text(found, e["mode"])
        ttk.Label(main, text=f"{e['name']} → {version}: {plan.summary()}").pack(anchor="w")
        message = first_line(found.get("message"))
        if message:
            ttk.Label(main, text=message, style="Muted.TLabel", wraplength=640).pack(anchor="w")
        if plan.drastic():
            ttk.Label(main, style="Error.TLabel", wraplength=640, text=(
                f"This removes {len(plan.delete)} of the {plan.target_files} files in "
                f"{e['target']}. Check that's the right folder (they're backed up, so Undo "
                "brings them back).")).pack(anchor="w", pady=(6, 0))
        lst = ttk.Frame(main)
        lst.pack(fill="both", expand=True, pady=(8, 8))
        tree = ttk.Treeview(lst, columns=("change",), height=14, show="tree headings")
        tree.heading("#0", text="File", anchor="w")
        tree.heading("change", text="Change", anchor="w")
        tree.column("#0", width=480)
        tree.column("change", width=110)
        scroll = ttk.Scrollbar(lst, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        for rels, word in ((plan.change, "changed"), (plan.add, "new"), (plan.delete, "removed")):
            for rel in rels:
                tree.insert("", "end", text=rel, values=(word,))
        buttons = ttk.Frame(main)
        buttons.pack(fill="x")
        ttk.Label(buttons, style="Muted.TLabel",
                  text="What it replaces or removes is backed up first.").pack(side="left")
        ttk.Button(buttons, text="Cancel", command=lambda: self._answer(False)).pack(side="right")
        go = ttk.Button(buttons, text="Update", command=lambda: self._answer(True))
        go.pack(side="right", padx=6)
        self.protocol("WM_DELETE_WINDOW", lambda: self._answer(False))
        self.bind("<Escape>", lambda ev: self._answer(False))
        self.bind("<Return>", lambda ev: self._answer(True))
        self.grab_set()
        go.focus_set()

    def _answer(self, ok):
        if self.answered:
            return
        self.answered = True
        self.grab_release()
        self.destroy()
        self.on_done(ok)


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Settings")
        self.transient(app.root)
        self.resizable(False, False)
        theme.style_window(self)
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="GitHub token (optional)").pack(anchor="w")
        self.token = tk.StringVar(value=theme.load_setting("token", ""))
        ttk.Entry(main, textvariable=self.token, width=56, show="•").pack(fill="x", pady=4)
        ttk.Label(main, style="Muted.TLabel", wraplength=420, justify="left", text=(
            "Only needed for private repos, or if you check very often (GitHub allows 60 "
            "checks an hour without one). Make a fine-grained token on GitHub (Settings → "
            "Developer settings) with read-only access to Contents. It's stored in your user "
            "profile on this PC.")).pack(anchor="w")
        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right", padx=6)
        self.grab_set()

    def _save(self):
        theme.save_setting("token", self.token.get().strip())
        self.destroy()
        self.app.check_all()


def open_path(path):
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


def asset(name):
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(__file__), os.pardir))
    return os.path.join(base, "assets", name)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        from .selftest import run

        sys.exit(run(sys.argv[2]))
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    theme.apply(root, theme.load_dark_setting())
    GameUpdaterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
