"""Single source of truth for paths and the one model endpoint.

Everything runs against the llama-swap endpoint — no cloud keys, no
per-machine copies of secrets. SMB credentials come from the encrypted
synology-mcp vault (same env var as the running MCP server).
"""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

NAS_HOST_DEFAULT = "192.168.178.9"
NAS_SHARE_DEFAULT = "Literatur"   # the share holding the library
NAS_SHARES_ENV = "SYNOLOGY_NAS_SHARE"   # optional override from the env file

# llama-swap — the ONLY model endpoint. Same server for the pageindex
# client and every VLM caption request.
OPENAI_BASE_URL = os.environ.get(
    "OPENAI_BASE_URL", "http://127.0.0.1:28080/v1")
MODEL_ID = "qwen38fn-sglang-tp2-starfleet"

STORE = ROOT / "store"              # trees/ + manifest.json
CACHE = ROOT / "cache"              # transient downloads, pruned after each doc
LOGS = ROOT / "logs"

# one entry per document in the manifest
STATUS_DONE = "done"
STATUS_FAILED = "failed"

def slug(rel: str) -> str:
    """Filesystem-safe id for a share-relative path like
    'SAP/2025Joule for Consultants.pdf' -> 'SAP__2025Joule for Consultants'."""
    s = rel.replace("/", "__")
    for ch in ("\\", "/", ":", "*", "?", '"', "<", ">", "|"):
        s = s.replace(ch, "_")
    if s.lower().endswith(".pdf"):
        s = s[:-4]
    return s
