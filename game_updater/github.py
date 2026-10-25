"""Talking to GitHub: repos, branches, releases and downloads.

Public repos need no login. A personal access token (optional) gives private
repos and a higher limit on checks (60 an hour without one, 5,000 with).
"""

import fnmatch
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .version import __version__

API = "https://api.github.com"
TIMEOUT = 20  # seconds
CHUNK = 256 * 1024


class GitHubError(Exception):
    """Something GitHub said no to, in words a person can act on."""


def parse_repo(text):
    """Pick a repo out of 'owner/repo' or a GitHub link.

    Returns (repo, rest): repo is 'owner/repo', and rest is whatever followed
    /tree/ in a link (a branch name and maybe a folder, which can't be told
    apart without the branch list: see split_tree_path). Raises ValueError.
    """
    text = text.strip()
    match = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)(/.*)?$", text)
    if match:
        owner, name, tail = match.group(1), match.group(2), match.group(3) or ""
    else:
        match = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$", text)
        if not match:
            raise ValueError("Enter the repo as owner/name, or paste its GitHub link.")
        owner, name, tail = match.group(1), match.group(2), ""
    name = name[:-4] if name.endswith(".git") else name
    rest = ""
    tree = re.match(r"^/(?:tree|blob)/(.+?)/?$", tail)
    if tree:
        rest = urllib.parse.unquote(tree.group(1))
    return f"{owner}/{name}", rest


def split_tree_path(rest, branches):
    """'claude/x/Assets/Scripts' -> ('claude/x', 'Assets/Scripts') using the branch names."""
    for branch in sorted(branches, key=len, reverse=True):
        if rest == branch or rest.startswith(branch + "/"):
            return branch, rest[len(branch) + 1:]
    first, _, path = rest.partition("/")
    return first, path


class GitHub:
    def __init__(self, token=""):
        self.token = token.strip()

    def _request(self, url, accept="application/vnd.github+json"):
        headers = {"User-Agent": f"GameUpdater/{__version__}", "Accept": accept}
        if self.token and url.startswith(API):
            headers["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(url, headers=headers)

    def get_json(self, path):
        try:
            with urllib.request.urlopen(self._request(API + path), timeout=TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raise GitHubError(self._explain(e)) from e
        except (urllib.error.URLError, OSError) as e:
            raise GitHubError(f"Couldn't reach GitHub ({getattr(e, 'reason', e)}).") from e

    def _explain(self, error):
        if error.code == 404:
            return ("Not found. Check the repo, branch and folder names"
                    + ("" if self.token else ", and add a token in Settings if the repo is private")
                    + ".")
        if error.code in (401, 403) and error.headers.get("X-RateLimit-Remaining") == "0":
            return ("GitHub's limit on checks has been reached for now (60 an hour without a "
                    "token). Try again later, or add a token in Settings.")
        if error.code == 401:
            return "GitHub didn't accept the token in Settings."
        return f"GitHub answered {error.code} {error.reason}."

    # -------------------------------------------------------------- repos
    def repo_info(self, repo):
        return self.get_json(f"/repos/{repo}")

    def branches(self, repo):
        """Branch names, most recent pages first (up to 300)."""
        names = []
        for page in range(1, 4):
            batch = self.get_json(f"/repos/{repo}/branches?per_page=100&page={page}")
            names += [b["name"] for b in batch]
            if len(batch) < 100:
                break
        return names

    def branch_head(self, repo, branch):
        """The newest commit on a branch: {version (sha), date, message}."""
        ref = urllib.parse.quote(branch, safe="")
        commit = self.get_json(f"/repos/{repo}/commits/{ref}")
        return {"version": commit["sha"], "date": commit["commit"]["committer"]["date"],
                "message": commit["commit"]["message"].strip(),
                "url": self.zip_url(repo, commit["sha"])}

    def zip_url(self, repo, ref):
        if self.token:  # the API can serve private repos with the token
            return f"{API}/repos/{repo}/zipball/{urllib.parse.quote(ref, safe='')}"
        return f"https://codeload.github.com/{repo}/zip/{urllib.parse.quote(ref, safe='')}"

    def latest_release(self, repo, asset_pattern=""):
        """The newest release and the file to get from it.

        asset_pattern picks an attached file by name (wildcards allowed, or just
        part of the name); without one, the only .zip attached is used, else
        the release's source code.
        """
        release = self.get_json(f"/repos/{repo}/releases/latest")
        assets = release.get("assets", [])
        asset = pick_asset(assets, asset_pattern)
        if asset_pattern and asset is None:
            names = ", ".join(a["name"] for a in assets) or "none"
            raise GitHubError(f"The latest release has no file matching “{asset_pattern}” "
                              f"(it has: {names}).")
        if asset is not None:
            url, name = asset["url"] if self.token else asset["browser_download_url"], asset["name"]
        else:
            url, name = self.zip_url(repo, release["tag_name"]), "source.zip"
        return {"version": release["tag_name"],
                "date": release.get("published_at") or release.get("created_at"),
                "message": (release.get("name") or release["tag_name"]).strip(),
                "url": url, "file": name}

    # ---------------------------------------------------------- download
    def download(self, url, dst, on_progress=None, should_cancel=None):
        """Save url to dst. Returns False if cancelled; raises GitHubError on failure."""
        accept = "application/octet-stream" if "/releases/assets/" in url else "*/*"
        try:
            with urllib.request.urlopen(self._request(url, accept), timeout=TIMEOUT) as response, \
                    open(dst, "wb") as f:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    if should_cancel and should_cancel():
                        return False
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
        except urllib.error.HTTPError as e:
            raise GitHubError(self._explain(e)) from e
        except (urllib.error.URLError, OSError) as e:
            raise GitHubError(f"The download failed ({getattr(e, 'reason', e)}).") from e
        if total and done < total:
            raise GitHubError("The download was cut short.")
        return True


def pick_asset(assets, pattern):
    if pattern:
        pattern = pattern.strip().lower()
        for asset in assets:
            name = asset["name"].lower()
            if fnmatch.fnmatch(name, pattern) or pattern in name:
                return asset
        return None
    zips = [a for a in assets if a["name"].lower().endswith(".zip")]
    return zips[0] if len(zips) == 1 else None
