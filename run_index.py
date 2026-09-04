#!/usr/bin/env python3
"""Literature index builder.

    python3 run_index.py full      # first run: everything
    python3 run_index.py refresh   # only what changed since the watermark

Per document: SMB download -> docling conversion (structure + tables + OCR)
-> page-image render -> per-image VLM caption (one endpoint: llama-swap)
-> PageIndex tree. State lives in store/manifest.json; trees in store/trees/.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import config
import naslib

# --------------------------------------------------------------------------
# env: API keys come from the shared env file, never from code
sys.path.insert(0, str(Path.home() / ".config" / "opencode"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(Path.home() / ".config" / "opencode" / ".env")

LLAMA = config.OPENAI_BASE_URL
MODEL = config.MODEL_ID

# docling: the standard pipeline covers text AND scanned pages.
from docling.datamodel.base_models import InputFormat  # noqa: E402
from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: E402
from docling.datamodel.accelerator_options import (  # noqa: E402
    AcceleratorOptions, AcceleratorDevice)
from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: E402

_image_re = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|html?|md|txt|epub)$", re.I)


def _converter() -> DocumentConverter:
    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    # the GPU belongs to llama-swap; never evict served models for batch jobs
    opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def llm_chat(messages: list) -> str:
    """One chat turn on the single endpoint. Returns assistant text."""
    from openai import OpenAI
    client = OpenAI(base_url=LLAMA, api_key=os.environ.get("OPENAI_API_KEY", "none"))
    resp = client.chat.completions.create(
        model=MODEL, messages=messages, max_tokens=600)
    return resp.choices[0].message.content or ""


def content_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_pages(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                         text=True, check=False).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else 0


def render_page(pdf: Path, page: int, out_dir: Path) -> Path:
    """Render one page to PNG; return the actual file pdftoppm wrote.

    pdftoppm appends its own page suffix to the output prefix, so the
    produced name is not the stem we pass in — glob the one-file dir.
    """
    out = out_dir / f"p{page:03d}"
    out.mkdir(parents=True, exist_ok=True)
    existing = sorted(out.glob("*.png"))
    if existing:
        return existing[0]
    subprocess.run(["pdftoppm", "-png", "-r", "96", "-f", str(page),
                    "-l", str(page), str(pdf), str(out / "pg")], check=True)
    return sorted(out.glob("*.png"))[0]


def page_sizes(pdf: Path) -> dict:
    """{page_no: (w_pt, h_pt)} from pdfinfo, in PDF point space."""
    out = subprocess.run(["pdfinfo", "-l", "400", str(pdf)],
                         capture_output=True, text=True, check=False).stdout
    sizes = {}
    for n, w, h in re.findall(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+) pts", out):
        sizes[int(n)] = (float(w), float(h))
    return sizes


def crop(png: Path, bbox: dict, out: Path, page_wh: tuple) -> None:
    """Crop a page PNG (rendered at 96 dpi) from a PDF-space bbox.

    docling reports bboxes in PDF points, BOTTOMLEFT origin; PIL crops need
    top-down coords -> y_pil = page_h - y_pdf. Points scale by 96/72.
    """
    from PIL import Image
    w_pt, h_pt = page_wh
    s = 96 / 72.0
    im = Image.open(png)
    x0, x1 = sorted((int(bbox["l"] * s), int(bbox["r"] * s)))
    top, bot = int((h_pt - bbox["t"]) * s), int((h_pt - bbox["b"]) * s)
    if top > bot:
        top, bot = bot, top
    box = (max(0, x0), max(0, top),
           min(int(w_pt * s), x1), min(int(h_pt * s), bot))
    im.crop(box).save(out)


def _collect_pictures(node, acc: list) -> None:
    if isinstance(node, dict):
        if node.get("label") == "picture" and node.get("prov"):
            acc.append(node)
        for v in node.values():
            _collect_pictures(v, acc)
    elif isinstance(node, list):
        for v in node:
            _collect_pictures(v, acc)


def process_doc(nas, rel: str, fullpath: str, conv: DocumentConverter,
                tmp: Path) -> dict:
    """Returns {status, pages, images_captions} for the manifest."""
    slug = config.slug(rel)
    local = tmp / f"{slug}.pdf"
    if not local.exists():
        nas.download(fullpath, local)

    result = conv.convert(str(local))
    doc = result.document
    structure = doc.export_to_dict()

    # ---- images: render once per page, crop per item, caption via LLM
    sizes = page_sizes(local)
    FALLBACK = (595.0, 842.0)  # A4 in pt, if pdfinfo lacks a page
    captions: dict = {}
    page_cache: dict = {}
    pics: list = []
    _collect_pictures(structure, pics)
    for item in pics:
        prov = (item.get("prov") or [{}])[0]
        page = int(prov.get("page_no", 1))
        if page not in page_cache:
            page_cache[page] = render_page(local, page, tmp / f"{slug}_pages")
        crop_path = tmp / f"{slug}_pages" / f"c{len(captions):03d}.png"
        crop(page_cache[page], prov["bbox"], crop_path,
             sizes.get(page, FALLBACK))
        caption = llm_chat([{"role": "user", "content": [
            {"type": "text", "text":
             "This is one figure cut from a PDF. Describe it precisely; if "
             "it is a table or chart, transcribe the labels and data."},
            {"type": "image_url", "image_url": {"url":
             "data:image/png;base64,"
             + base64.b64encode(crop_path.read_bytes()).decode()}}]}])
        captions[item.get("self_ref", f"p{page}c{len(captions)}")] = {
            "page": page, "caption": caption, "file": str(crop_path.name)}

    # The docling structure IS the page index (headings, tables, figures
    # with page+bbox). A PageIndex tree was tested and drops: it duplicates
    # this export, and its extra per-section LLM calls hit the llama-swap
    # rate limit (429 after 10 retries, measured 2026-09-03).

    out = config.STORE / "documents" / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "docling.json").write_text(
        json.dumps(structure, ensure_ascii=False, indent=1))
    (out / "captions.json").write_text(
        json.dumps(captions, ensure_ascii=False, indent=1))
    shutil.rmtree(tmp / f"{slug}_pages", ignore_errors=True)

    return {"status": "done",
            "pages": pdf_pages(local), "images": len(captions),
            "hash": content_hash(local)}


def walk_entries(nas: naslib.NAS) -> dict:
    """Share-relative path -> {size, mtime}, for every file under the share."""
    out = {}
    for e in nas.walk(""):
        name = Path(e["path"]).name
        # Office save-lock stubs (~$x.docx) and empty files are not content
        if name == ".DS_Store" or name.startswith("~$") or e["size"] == 0:
            continue
        if _image_re.search(name):
            rel = e["path"].lstrip("/")
            out[rel] = {"size": e["size"], "mtime": e["mtime"]}
    return out


def load_manifest() -> dict:
    p = config.STORE / "manifest.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"watermark": None, "docs": {}}


def save_manifest(m: dict) -> None:
    (config.STORE / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["full", "refresh"])
    ap.add_argument("--limit", type=int, default=0,
                    help="process only the first N docs (smoke tests)")
    ap.add_argument("--only", default="", metavar="PATH",
                    help="share-relative path of ONE doc to process")
    args = ap.parse_args()

    config.STORE.mkdir(exist_ok=True)
    config.CACHE.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lit_", dir=str(config.CACHE)))
    tmp.mkdir(exist_ok=True)
    nas = naslib.NAS()
    conv = _converter()

    manifest = load_manifest()
    known = manifest.get("docs", {})
    current = walk_entries(nas)

    if args.only:
        if args.only.strip("/") not in current:
            sys.exit(f"not on share: {args.only!r}")
        queue = [args.only.strip("/")]
    elif args.mode == "full":
        queue = list(current)
    else:
        queue = [p for p, st in current.items()
                 if p not in known or st["size"] != known[p]["size"]
                 or int(st["mtime"]) > int(float(str(known[p].get("mtime") or 0)))]

    if args.limit:
        queue = queue[:args.limit]

    done = 0
    for rel in queue:
        try:
            info = process_doc(nas, rel, "/" + rel, conv, tmp)
            status = info["status"]
        except Exception as exc:  # one bad file must not end the run
            info, status = {"error": f"{type(exc).__name__}: {exc}"}, "failed"
        known[rel] = {"size": current[rel]["size"],
                      "mtime": current[rel]["mtime"], **info}
        done += 1
        print(f"[{done}] {rel} -> {status}", flush=True)

    manifest["watermark"] = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"done: {done} docs, watermark updated")


if __name__ == "__main__":
    main()
