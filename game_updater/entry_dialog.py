"""The window for adding or changing a game: where to get it and where it goes."""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import github
from . import sync
from . import theme
from .tooltip import Tooltip


class EntryDialog(tk.Toplevel):
    """Edits a copy of entry (a dict); on_save(entry) gets it when Save is clicked."""

    def __init__(self, app, entry, on_save):
        super().__init__(app.root)
        self.app, self.on_save = app, on_save
        self.entry = dict(entry)
        self.original_target = entry.get("target", "")
        self.title("Change game" if entry.get("repo") else "Add a game")
        self.transient(app.root)
        self.resizable(True, False)
        theme.style_window(self)
        self.lookup_token = 0
        e = self.entry
        self.vars = {
            "name": tk.StringVar(value=e.get("name", "")),
            "repo": tk.StringVar(value=e.get("repo", "")),
            "mode": tk.StringVar(value=e.get("mode", "branch")),
            "branch": tk.StringVar(value=e.get("branch", "")),
            "asset": tk.StringVar(value=e.get("asset", "")),
            "subfolder": tk.StringVar(value=e.get("subfolder", "")),
            "target": tk.StringVar(value=e.get("target", "")),
            "mirror": tk.BooleanVar(value=e.get("mirror", False)),
            "protect": tk.StringVar(value=e.get("protect", "")),
        }
        self._build()
        self._mode_changed()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda ev: self.destroy())
        if e.get("repo"):
            self.lookup(quiet=True)
        self.grab_set()
        self.repo_entry.focus_set()

    def _build(self):
        v = self.vars
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        row = 0

        def label(text):
            ttk.Label(main, text=text).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)

        label("GitHub repo")
        repo = ttk.Frame(main)
        repo.grid(row=row, column=1, sticky="ew", pady=4)
        self.repo_entry = ttk.Entry(repo, textvariable=v["repo"], width=48)
        self.repo_entry.pack(side="left", fill="x", expand=True)
        self.repo_entry.bind("<Return>", lambda ev: self.lookup())
        self.repo_entry.bind("<FocusOut>", lambda ev: self.lookup(quiet=True))
        Tooltip(self.repo_entry, "owner/name, or paste the repo's link (a link to a folder "
                                 "on a branch fills in the branch and folder too)")
        ttk.Button(repo, text="Look up", command=self.lookup).pack(side="left", padx=(6, 0))
        row += 1
        self.repo_note = ttk.Label(main, style="Muted.TLabel")
        self.repo_note.grid(row=row, column=1, sticky="w")
        row += 1

        label("Name")
        ttk.Entry(main, textvariable=v["name"]).grid(row=row, column=1, sticky="ew", pady=4)
        row += 1

        label("Get")
        get = ttk.Frame(main)
        get.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Radiobutton(get, text="The latest on branch", value="branch", variable=v["mode"],
                        command=self._mode_changed).pack(side="left")
        self.branch_box = ttk.Combobox(get, textvariable=v["branch"], width=34)
        self.branch_box.pack(side="left", padx=(6, 16))
        Tooltip(self.branch_box, "Every change pushed to this branch is an update")
        ttk.Radiobutton(get, text="The latest release", value="release", variable=v["mode"],
                        command=self._mode_changed).pack(side="left")
        row += 1
        self.asset_label = ttk.Label(main, text="Release file")
        self.asset_label.grid(row=row, column=0, sticky="w", pady=4)
        self.asset_entry = ttk.Entry(main, textvariable=v["asset"])
        self.asset_entry.grid(row=row, column=1, sticky="ew", pady=4)
        Tooltip(self.asset_entry, "Which file attached to the release to get, e.g. windows.zip "
                                  "(part of the name is enough). Blank: its only .zip, "
                                  "else the source code. Zips are unpacked; other files "
                                  "are copied as they are.")
        row += 1

        label("Folder in the repo")
        sub = ttk.Entry(main, textvariable=v["subfolder"])
        sub.grid(row=row, column=1, sticky="ew", pady=4)
        Tooltip(sub, "Copy just this folder, e.g. Assets/Scripts. Blank: everything.")
        row += 1

        label("Copy into")
        target = ttk.Frame(main)
        target.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Entry(target, textvariable=v["target"]).pack(side="left", fill="x", expand=True)
        ttk.Button(target, text="Browse...", command=self._browse).pack(side="left", padx=(6, 0))
        row += 1

        mirror = ttk.Checkbutton(main, text="Remove files that are no longer in the repo",
                                 variable=v["mirror"])
        mirror.grid(row=row, column=1, sticky="w", pady=(8, 2))
        Tooltip(mirror, "Keeps the folder an exact copy, so renamed or deleted files don't "
                        "linger (in Unity, stale scripts cause compile errors). Unity's .meta "
                        "files stay for everything that's still there. Removed files are "
                        "backed up, so Undo brings them back.")
        row += 1

        label("Never touch")
        protect = ttk.Entry(main, textvariable=v["protect"])
        protect.grid(row=row, column=1, sticky="ew", pady=4)
        Tooltip(protect, "Files or folders that are never changed or removed, separated by "
                         "semicolons, e.g. settings.ini; saves/*; *.cfg")
        row += 1

        buttons = ttk.Frame(main)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Save", command=self._save).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=(6, 0))

    def _mode_changed(self):
        release = self.vars["mode"].get() == "release"
        self.branch_box.config(state="disabled" if release else "normal")
        self.asset_entry.config(state="normal" if release else "disabled")

    def _browse(self):
        folder = filedialog.askdirectory(parent=self, title="Folder to copy into",
                                         initialdir=self.vars["target"].get() or None)
        if folder:
            self.vars["target"].set(os.path.normpath(folder))

    # ------------------------------------------------------------ lookup
    def lookup(self, quiet=False):
        """Check the repo exists and fill in its branches (in the background)."""
        try:
            repo, rest = github.parse_repo(self.vars["repo"].get())
        except ValueError as e:
            if not quiet:
                self.repo_note.config(text=str(e), style="Error.TLabel")
            return
        self.vars["repo"].set(repo)
        self.lookup_token += 1
        token = self.lookup_token
        self.repo_note.config(text="Looking it up...", style="Muted.TLabel")
        hub = self.app.github()

        def work():
            try:
                info = hub.repo_info(repo)
                branches = hub.branches(repo)
                result = (info, branches, None)
            except github.GitHubError as e:
                result = (None, None, str(e))
            self.app.from_thread(lambda: self._looked_up(token, repo, rest, *result))

        threading.Thread(target=work, daemon=True).start()

    def _looked_up(self, token, repo, rest, info, branches, error):
        if token != self.lookup_token or not self.winfo_exists():
            return
        if error:
            self.repo_note.config(text=error, style="Error.TLabel")
            return
        default = info.get("default_branch", "")
        others = sorted(b for b in branches if b != default)
        self.branch_box.config(values=[default] + others)
        if rest:  # a link to a branch (and maybe a folder in it)
            branch, path = github.split_tree_path(rest, branches)
            self.vars["branch"].set(branch)
            if path:
                self.vars["subfolder"].set(path)
        elif not self.vars["branch"].get():
            self.vars["branch"].set(default)
        if not self.vars["name"].get():
            self.vars["name"].set(repo.split("/")[1].replace("-", " ").replace("_", " "))
        private = " (private)" if info.get("private") else ""
        self.repo_note.config(text=f"Found{private}: {len(branches)} branch"
                                   f"{'es' if len(branches) != 1 else ''}, the main one is "
                                   f"“{default}”.", style="Muted.TLabel")

    # -------------------------------------------------------------- save
    def _save(self):
        v = self.vars
        try:
            repo, _ = github.parse_repo(v["repo"].get())
        except ValueError as e:
            messagebox.showerror("Add a game", str(e), parent=self)
            return
        mode = v["mode"].get()
        target = v["target"].get().strip()
        if mode == "branch" and not v["branch"].get().strip():
            messagebox.showerror("Add a game", "Pick the branch to get (Look up lists them).",
                                 parent=self)
            return
        problem = sync.check_target(target, v["mirror"].get())
        if problem:
            messagebox.showerror("Add a game", problem, parent=self)
            return
        better = sync.unity_target(target, v["subfolder"].get())
        if better and messagebox.askyesno(
                "Copy into", f"That's a Unity project, so the files would land beside its "
                f"real {os.path.basename(better)} folder, where Unity ignores them or sees "
                f"every script twice (and stops compiling).\n\nCopy into\n{better}\n"
                "instead?", parent=self):
            target = better
            v["target"].set(better)
        if sync.built_game(target) and not messagebox.askyesno(
                "Copy into", "That folder is a built game (it has UnityPlayer.dll). Scripts "
                "copied there do nothing until the game is built again in Unity: pick the "
                "Unity project's folder instead.\n\nKeep this folder anyway?", parent=self):
            return
        if not os.path.isdir(target) and not messagebox.askyesno(
                "Add a game", f"“{target}” doesn't exist yet. Create it on the first update?",
                parent=self):
            return
        source = (mode, v["branch"].get().strip(), v["asset"].get().strip(),
                  v["subfolder"].get().strip().strip("/\\"))
        before = (self.entry.get("mode"), self.entry.get("branch", ""),
                  self.entry.get("asset", ""), self.entry.get("subfolder", ""))
        changed_source = repo != self.entry.get("repo") or source != before
        self.entry.update(
            name=v["name"].get().strip() or repo.split("/")[1], repo=repo, mode=mode,
            branch=source[1], asset=source[2], subfolder=source[3], target=target,
            mirror=v["mirror"].get(), protect=v["protect"].get().strip())
        if changed_source or os.path.normcase(target) != os.path.normcase(self.original_target):
            self.entry["installed"] = None  # a different source: compare from scratch
        self.destroy()
        self.on_save(self.entry)
