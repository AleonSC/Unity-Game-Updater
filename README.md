# Game Updater

A small Windows app that keeps game folders up to date from GitHub with one click.
Point it at a repo and a folder on your PC; **Update** downloads the latest version
and copies it in, backing up everything it replaces so **Undo last update** can put
it back.

Made for keeping a Unity project's `Assets/Scripts` in step with the code on GitHub,
but it works for any folder: a game's files, mods, tools.

## Adding a game

Click **Add...** and paste the repo's GitHub link. A link to a folder on a branch
(e.g. `https://github.com/AleonSC/PixelClicker/tree/<branch>/Assets/Scripts`) fills in
the branch and folder too. Then:

- **Get:** *the latest on branch* (every push to that branch is an update), or
  *the latest release* (optionally a file attached to it, e.g. `windows.zip`; zips are
  unpacked, other files copied as they are).
- **Folder in the repo:** copy just this folder, e.g. `Assets/Scripts`. Blank copies everything.
- **Copy into:** the folder on your PC, e.g. your Unity project's `Assets\Scripts`.
- **Remove files that are no longer in the repo:** keeps the folder an exact copy, so
  renamed or deleted scripts don't linger and cause compile errors. Unity's `.meta`
  files stay for everything that's still there, and go with the files that are removed.
- **Never touch:** files or folders that are never changed or removed, e.g.
  `settings.ini; saves/*; *.cfg`.

## Updating

It checks for updates when it starts (or press **Check** / F5). The list shows what's
installed, the latest version and whether it's up to date. **Update** (or Enter) updates
the selected game; **Update all** does every one.

With **Show changes first** ticked, it lists what will change (changed, new and removed
files) and asks before touching anything. An update that would remove most of a folder
always asks, in case the wrong folder was picked. If a file can't be replaced (the game
or Unity is using it, say), nothing is changed. The last five updates of each game are
backed up in `%APPDATA%\GameUpdater\backups`.

Unity can stay open while it updates; it picks up the new scripts when you switch back
to it.

## Private repos

Public repos need nothing. For a private repo (or more than 60 checks an hour), add a
GitHub token in **Settings...**: a fine-grained token with read-only access to Contents.

## Installing

Download **GameUpdater-Setup.exe** from the latest release
(https://github.com/AleonSC/unity-game-updater/releases/latest). It installs for your user
only (no admin password). When a newer Game Updater is out, a button at the bottom right
updates it in place. **GameUpdater-portable.zip** runs without installing but can't
update itself.

Every push to the default branch builds a numbered release on GitHub Actions
(`.github/workflows/build-windows.yml`), after self-testing the app and its installer.

### Building it yourself

On Windows with Python 3.10+, run `build.bat`. Running from source needs nothing but
Python: `python run_game_updater.py`.
