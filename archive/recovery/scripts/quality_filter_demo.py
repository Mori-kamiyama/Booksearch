"""
Crop quality filter tuning UI.

Usage:
  uv run python scripts/quality_filter_demo.py
  uv run python scripts/quality_filter_demo.py --catalog outputs/book_catalog_quality_check/catalog.json
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = "outputs/book_catalog_quality_check/catalog.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune crop quality thresholds in a browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4175)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    return parser.parse_args()


def resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def safe_repo_path(raw_path: str) -> Path:
    path = Path(urllib.parse.unquote(raw_path.lstrip("/")))
    full_path = (REPO_ROOT / path).resolve()
    if REPO_ROOT.resolve() not in full_path.parents and full_path != REPO_ROOT.resolve():
        raise FileNotFoundError(raw_path)
    return full_path


def rel_path(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value)
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def load_catalog(catalog_path: Path) -> dict[str, Any]:
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    entries = []
    for entry in data.get("entries", []):
        quality = entry.get("crop_quality") or {}
        entries.append(
            {
                "box_id": entry.get("box_id"),
                "source_image": rel_path(entry.get("source_image")),
                "crop_image": rel_path(entry.get("crop_image")),
                "detector_confidence": entry.get("detector_confidence"),
                "bbox_xyxy": entry.get("bbox_xyxy"),
                "quality": quality,
                "books": [book.get("title") for book in entry.get("books", []) if book.get("title")],
            }
        )
    return {
        "catalog": str(catalog_path.relative_to(REPO_ROOT)),
        "source": data.get("source"),
        "detector_model": data.get("detector_model"),
        "entries": entries,
    }


def page(catalog_path: Path) -> bytes:
    title = f"Quality Filter - {catalog_path.name}"
    doc = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --ink: #1d242b;
      --muted: #66717d;
      --line: #d7dee6;
      --ok: #1f8f5f;
      --bad: #b5472d;
      --warn: #a66b00;
      --focus: #2367c7;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 5;
      border-bottom: 1px solid var(--line);
      background: rgba(244, 246, 248, 0.96);
      backdrop-filter: blur(10px);
    }}
    .bar {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto;
      gap: 18px;
      align-items: center;
      max-width: 1440px;
      margin: 0 auto;
      padding: 14px 18px;
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      line-height: 1.25;
      font-weight: 700;
    }}
    .meta {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 3px;
      overflow-wrap: anywhere;
    }}
    .stats {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .stat {{
      min-width: 88px;
      border: 1px solid var(--line);
      background: var(--panel);
      padding: 8px 10px;
      border-radius: 8px;
      text-align: right;
    }}
    .stat strong {{ display: block; font-size: 18px; line-height: 1; }}
    .stat span {{ color: var(--muted); font-size: 11px; }}
    main {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 18px;
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr);
      gap: 18px;
    }}
    aside {{
      position: sticky;
      top: 92px;
      align-self: start;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .control {{ padding: 12px 0; border-top: 1px solid var(--line); }}
    .control:first-child {{ border-top: 0; padding-top: 0; }}
    label {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 13px;
      font-weight: 650;
      margin-bottom: 8px;
    }}
    label output {{ color: var(--focus); font-variant-numeric: tabular-nums; }}
    input[type="range"] {{ width: 100%; }}
    .check {{
      display: grid;
      grid-template-columns: 18px 1fr;
      gap: 8px;
      align-items: start;
      color: var(--ink);
      font-size: 13px;
      line-height: 1.35;
      margin: 10px 0;
    }}
    .hint {{ color: var(--muted); font-size: 12px; line-height: 1.45; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 12px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      min-width: 0;
    }}
    .card.excluded {{ border-color: #e2b7a8; background: #fff8f5; }}
    .thumb {{
      width: 100%;
      aspect-ratio: 1 / 1;
      background: #e8edf2;
      object-fit: contain;
      display: block;
    }}
    .info {{ padding: 10px; }}
    .row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }}
    .boxid {{
      font-size: 12px;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}
    .badge {{
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 3px 7px;
      font-size: 11px;
      font-weight: 700;
      color: #fff;
      background: var(--ok);
    }}
    .excluded .badge {{ background: var(--bad); }}
    .metrics {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
      font-size: 12px;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}
    .reason {{
      margin-top: 8px;
      min-height: 18px;
      color: var(--bad);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .books {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
      max-height: 3.9em;
      overflow: hidden;
    }}
    @media (max-width: 840px) {{
      .bar {{ grid-template-columns: 1fr; }}
      .stats {{ justify-content: flex-start; }}
      main {{ grid-template-columns: 1fr; padding: 12px; }}
      aside {{ position: static; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <div>
        <h1>Crop Quality Filter</h1>
        <div class="meta" id="catalogLabel">{html.escape(str(catalog_path.relative_to(REPO_ROOT)))}</div>
      </div>
      <div class="stats">
        <div class="stat"><strong id="includedCount">0</strong><span>include</span></div>
        <div class="stat"><strong id="excludedCount">0</strong><span>exclude</span></div>
        <div class="stat"><strong id="edgeCount">0</strong><span>edge</span></div>
      </div>
    </div>
  </header>
  <main>
    <aside>
      <div class="control">
        <label for="blur">Min blur score <output id="blurOut">120</output></label>
        <input id="blur" type="range" min="0" max="1200" step="10" value="120">
        <div class="hint">低いほどぼやけ。上げるとピンぼけ疑いをより強く除外します。</div>
      </div>
      <div class="control">
        <label for="shortEdge">Min short edge <output id="shortOut">320</output></label>
        <input id="shortEdge" type="range" min="0" max="1000" step="10" value="320">
        <div class="hint">crop短辺の最小px。小さすぎる箱をOCR対象から外します。</div>
      </div>
      <div class="control">
        <label for="conf">Min detector confidence <output id="confOut">0.25</output></label>
        <input id="conf" type="range" min="0" max="1" step="0.01" value="0.25">
      </div>
      <div class="control">
        <label class="check"><input id="rejectEdge" type="checkbox"><span>画像端に接する box を除外</span></label>
        <label class="check"><input id="showExcluded" type="checkbox" checked><span>除外された box も表示</span></label>
        <label class="check"><input id="showIncluded" type="checkbox" checked><span>残る box を表示</span></label>
      </div>
    </aside>
    <section class="grid" id="grid"></section>
  </main>
  <script>
    const state = {{
      entries: [],
      blur: 120,
      shortEdge: 320,
      conf: 0.25,
      rejectEdge: false,
      showExcluded: true,
      showIncluded: true,
    }};

    const els = {{
      grid: document.querySelector("#grid"),
      blur: document.querySelector("#blur"),
      shortEdge: document.querySelector("#shortEdge"),
      conf: document.querySelector("#conf"),
      rejectEdge: document.querySelector("#rejectEdge"),
      showExcluded: document.querySelector("#showExcluded"),
      showIncluded: document.querySelector("#showIncluded"),
      blurOut: document.querySelector("#blurOut"),
      shortOut: document.querySelector("#shortOut"),
      confOut: document.querySelector("#confOut"),
      includedCount: document.querySelector("#includedCount"),
      excludedCount: document.querySelector("#excludedCount"),
      edgeCount: document.querySelector("#edgeCount"),
    }};

    function imageUrl(path) {{
      return "/file/" + encodeURIComponent(path).replaceAll("%2F", "/");
    }}

    function evaluate(entry) {{
      const q = entry.quality || {{}};
      const reasons = [];
      if ((q.blur_score || 0) < state.blur) reasons.push("blurry");
      if ((q.short_edge || 0) < state.shortEdge) reasons.push("too_small");
      if ((entry.detector_confidence || 0) < state.conf) reasons.push("low_confidence");
      if (state.rejectEdge && (q.edge_touch || []).length) reasons.push("edge_touch");
      return {{ included: reasons.length === 0, reasons }};
    }}

    function render() {{
      els.blurOut.value = state.blur;
      els.shortOut.value = state.shortEdge;
      els.confOut.value = state.conf.toFixed(2);
      let included = 0;
      let excluded = 0;
      let edge = 0;
      const cards = [];
      for (const entry of state.entries) {{
        const q = entry.quality || {{}};
        if ((q.edge_touch || []).length) edge += 1;
        const result = evaluate(entry);
        if (result.included) included += 1; else excluded += 1;
        if (result.included && !state.showIncluded) continue;
        if (!result.included && !state.showExcluded) continue;
        const cls = result.included ? "card" : "card excluded";
        const badge = result.included ? "include" : "exclude";
        const reasonText = result.reasons.join(", ");
        const books = (entry.books || []).slice(0, 4).join(" / ");
        cards.push(`
          <article class="${{cls}}">
            <img class="thumb" src="${{imageUrl(entry.crop_image)}}" alt="">
            <div class="info">
              <div class="row">
                <div class="boxid">${{entry.box_id}}</div>
                <div class="badge">${{badge}}</div>
              </div>
              <div class="metrics">
                <div>blur ${{Math.round(q.blur_score || 0)}}</div>
                <div>short ${{q.short_edge || 0}}px</div>
                <div>conf ${{(entry.detector_confidence || 0).toFixed(2)}}</div>
                <div>edge ${{(q.edge_touch || []).join("/") || "-"}}</div>
              </div>
              <div class="reason">${{reasonText}}</div>
              <div class="books">${{books}}</div>
            </div>
          </article>
        `);
      }}
      els.includedCount.textContent = included;
      els.excludedCount.textContent = excluded;
      els.edgeCount.textContent = edge;
      els.grid.innerHTML = cards.join("");
    }}

    function bindRange(input, key, output) {{
      input.addEventListener("input", () => {{
        state[key] = Number(input.value);
        render();
      }});
    }}

    bindRange(els.blur, "blur");
    bindRange(els.shortEdge, "shortEdge");
    bindRange(els.conf, "conf");
    for (const [input, key] of [[els.rejectEdge, "rejectEdge"], [els.showExcluded, "showExcluded"], [els.showIncluded, "showIncluded"]]) {{
      input.addEventListener("change", () => {{
        state[key] = input.checked;
        render();
      }});
    }}

    fetch("/api/catalog")
      .then((response) => response.json())
      .then((data) => {{
        state.entries = data.entries || [];
        render();
      }});
  </script>
</body>
</html>"""
    return doc.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    catalog_path: Path

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(page(self.catalog_path), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/catalog":
            payload = json.dumps(load_catalog(self.catalog_path), ensure_ascii=False).encode("utf-8")
            self.send_bytes(payload, "application/json; charset=utf-8")
            return
        if parsed.path.startswith("/file/"):
            try:
                file_path = safe_repo_path(parsed.path.removeprefix("/file/"))
                media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
                self.send_bytes(file_path.read_bytes(), media_type)
            except OSError:
                self.send_error(404)
            return
        self.send_error(404)

    def send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    args = parse_args()
    catalog_path = resolve_path(args.catalog)
    if not catalog_path.exists():
        raise FileNotFoundError(f"catalog not found: {catalog_path}")
    Handler.catalog_path = catalog_path
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Quality filter demo: http://{args.host}:{args.port}/")
    print(f"catalog: {catalog_path}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())