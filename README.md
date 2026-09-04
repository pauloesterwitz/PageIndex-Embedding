# PageIndex-Embedding

Indexing pipeline for a large document library on a Synology NAS (SMB3).
One pass per file: content retrieval, structure extraction, figure detection
with per-figure visual description, and a durable manifest that makes reruns
skip what hasn't changed.

## Pipeline

1. **Inventory** — recursive metadata walk over the SMB share; Office save-lock
   stubs and empty files are filtered out at listing time.
2. **Convert** — docling (CPU-only; never touches the GPU) yields the full
   document structure: headings, sections, tables, figures — each item with
   page number and pixel-precise bbox, coordinates corrected for the
   bottom-left PDF origin before any raster work.
3. **Caption** — figures are rasterized per page, cropped per item, and sent to
   a locally hosted vision model (single llama-swap endpoint, no cloud).
4. **State** — `store/manifest.json` records size, mtime, content hash and
   status per document; the refresh pass only reprocesses what changed.

## Layout

- `config.py` — paths + the single model endpoint
- `naslib.py` — SMB connection + recursive walk
- `run_index.py` — CLI: `full`, `refresh`, `--only <path>`
- `query.py` — read-side: list the index, full-text hit search
- `watch.sh` — progress reporting to a chat channel

## Requirements

- Python 3.12, `docling`, `pysmb`, `pillow`, `python-dotenv`
- `poppler-utils` (pdftoppm / pdfinfo)
- LibreOffice for Office-format pre-conversion
- An OpenAI-compatible model endpoint with vision (v1/chat/completions)

## Notes

- Source corpora and API credentials are never committed.
