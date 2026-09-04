"""SMB3 access to the Literatur share, reusing the synology-mcp stack.

The old server only exposed per-file ops. This adds the two primitives a
corpus pipeline needs: a recursive metadata walk and a content download,
both on one authenticated session.
"""
from __future__ import annotations

import os
import sys
from collections import deque
from pathlib import Path

_PROJECT = Path("/home/pauloesterwitz/Bosch/Agentic Platforms Research/synology-nas-mcp")
sys.path.insert(0, str(_PROJECT))

import config
import vault
from smb_manager import SMBManager, SMBError


class NAS:
    def __init__(self) -> None:
        creds = vault.load_credentials()
        self.host = creds["host"]
        shares = creds.get("shares") or [config.NAS_SHARE_DEFAULT]
        self.share = shares[0] if shares else config.NAS_SHARE_DEFAULT
        self.mgr = SMBManager(
            host=self.host,
            username=creds["username"],
            password=creds["password"],
            shares=shares,
        )
        self.mgr.connect()

    def children(self, rel: str = "") -> list[tuple[str, bool]]:
        """(name, is_folder) entries of one directory, '.'/'..' stripped."""
        out = []
        for e in self.mgr._conn.listPath(self.share, "/" + rel.lstrip("/")):
            if e.filename in (".", ".."):
                continue
            out.append((e.filename, bool(e.isDirectory)))
        return out

    def stat(self, rel: str) -> dict:
        """One stat via listPath of the parent dir + match on name."""
        parent, _, base = rel.rpartition("/")
        entries = self.mgr._conn.listPath(
            self.share, "/" + (parent or "").lstrip("/"))
        for e in entries:
            if e.filename == base:
                return {
                    "size": e.file_size,
                    # pysmb names vary by version; grab what exists
                    "mtime": getattr(e, "last_write_time", 0) or 0,
                    "is_folder": bool(e.isDirectory),
                }
        raise SMBError(f"no entry for {rel!r}")

    def walk(self, rel: str = "") -> list[dict]:
        """Recursive: every file under *rel* as {path, size, mtime}.

        Metadata-only walk — no file contents cross the wire. The whole
        library is one listing per directory, which is the slowest part
        anyway (SMB latency), but each hop is tiny.
        """
        found: list[dict] = []
        queue = deque(["/" + rel.lstrip("/")])
        while queue:
            cur = queue.popleft()
            try:
                entries = self.mgr._conn.listPath(self.share, _abs(cur))
            except Exception:
                continue  # unreadable dir: skip, don't abort the walk
            for e in entries:
                if e.filename in (".", ".."):
                    continue
                full = _join(cur, e.filename)
                if e.isDirectory:
                    queue.append(full)
                else:
                    found.append({
                        "path": full,
                        "size": e.file_size,
                        "mtime": getattr(e, "last_write_time", 0) or 0,
                    })
        return found

    def download(self, rel: str, dest: Path) -> Path:
        """Download one file. *rel* is share-relative, e.g. '/SAP/x.pdf'.

        SMBManager.download_file expects the full '/<share>/...' form, so we
        re-attach the share here; listPath instead takes the share-relative
        path directly (its first arg *is* the service/share).
        """
        rel = _norm(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.mgr.download_file("/" + self.share + "/" + rel, dest)
        return dest


def _join(cur: str, name: str) -> str:
    return (cur.rstrip("/") + "/" + name)


def _norm(path: str) -> str:
    """Share-relative ('Literatur/...') -> path without leading slash."""
    return path.lstrip("/")


def _abs(path: str) -> str:
    """Ensure a leading slash for listPath."""
    return path if path.startswith("/") else "/" + path
