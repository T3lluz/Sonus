"""Update checks against the GitLab repo, and applying them.

Every push to the tracked branch counts as an update: the panel compares the
commit it was installed from with the branch head on gitlab.com. Nothing is
downloaded until the user asks for it, and a failed check is silent — the
panel has to stay useful offline.

The installed commit comes from the checkout when there is one, and
otherwise from the stamp file install.sh leaves behind (installs made from a
local source tree carry no .git).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

PROJECT = os.environ.get("SONUSDECK_PROJECT", "T3lluz/Sonus")
BRANCH = os.environ.get("SONUSDECK_BRANCH", "main")

APP_ROOT = Path(__file__).resolve().parent.parent
STAMP_PATH = APP_ROOT / ".sonus-commit"

_TIMEOUT = 6.0
_GIT_TIMEOUT = 5.0


@dataclass(frozen=True)
class Status:
    """Outcome of one check. `state` is unknown | current | outdated."""

    state: str
    local: str = ""
    remote: str = ""


def short(commit: str) -> str:
    return commit[:7]


def _api_url() -> str:
    project = quote(PROJECT, safe="")
    return (
        f"https://gitlab.com/api/v4/projects/{project}"
        f"/repository/commits/{quote(BRANCH, safe='')}"
    )


def _install_url() -> str:
    return f"https://gitlab.com/{PROJECT}/-/raw/{BRANCH}/install.sh"


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Sonus"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read()


# ----- what is installed ------------------------------------------------


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(APP_ROOT), *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _from_stamp() -> tuple[str, str]:
    try:
        data = json.loads(STAMP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("commit") or ""), str(data.get("date") or "")


def local_commit() -> tuple[str, str]:
    """(commit, ISO date) of the running copy; empty strings when unknown."""
    if (APP_ROOT / ".git").exists():
        commit = _git("rev-parse", "HEAD")
        if commit:
            return commit, _git("log", "-1", "--format=%cI")
    return _from_stamp()


def remote_commit() -> tuple[str, str]:
    """(commit, ISO date) of the branch head. Raises OSError when offline."""
    payload = json.loads(_fetch(_api_url()).decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("id"):
        raise ValueError("unexpected commit payload")
    return str(payload["id"]), str(payload.get("committed_date") or "")


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def check() -> Status:
    try:
        remote, remote_date = remote_commit()
    except (OSError, ValueError, json.JSONDecodeError):
        return Status("unknown")
    local, local_date = local_commit()
    if not local:
        return Status("unknown", remote=remote)
    if local == remote:
        return Status("current", local, remote)
    # A checkout sitting ahead of the branch (development) is not outdated.
    here, there = _parse(local_date), _parse(remote_date)
    if here is not None and there is not None and here >= there:
        return Status("current", local, remote)
    return Status("outdated", local, remote)


# ----- applying ---------------------------------------------------------


def apply_update() -> bool:
    """Run the published installer detached; it updates, then restarts us.

    The script is dropped in its own temporary directory so it cannot be
    mistaken for a local source tree — that way it always fetches the branch
    instead of reinstalling whatever is on disk.
    """
    try:
        script = _fetch(_install_url())
    except (OSError, urllib.error.URLError):
        return False
    if not script.startswith(b"#!"):
        return False
    try:
        workdir = Path(tempfile.mkdtemp(prefix="sonus-update-"))
        path = workdir / "install.sh"
        path.write_bytes(script)
        path.chmod(stat.S_IRWXU)
        log = open(workdir / "install.log", "wb")
    except OSError:
        return False
    try:
        subprocess.Popen(
            ["bash", str(path)],
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError:
        log.close()
        return False
    log.close()
    return True
