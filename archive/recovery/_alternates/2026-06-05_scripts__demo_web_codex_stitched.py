"""
画像アップロードから、箱検出 -> OCR -> ローカル図書DB検索 -> Google Books書影表示まで行うデモUI。

使い方:
  zsh -lc 'source ~/.zshrc >/dev/null 2>&1; uv run python scripts/demo_web.py'
  open http://127.0.0.1:4174/
"""

from __future__ import annotations

import argparse
import cgi
import html
import json
import os
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from build_book_catalog import (
    DEFAULT_LIBRARY_DB,
    DEFAULT_YOLO_MODEL,
    detect_and_crop,
    gemini_ocr,
    library_db_lookup,
    resolve_path,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "outputs" / "demo_web"
CACHE_PATH = DEMO_DIR / "google_books_cache.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Booksearch demo web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4174)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--model", default=DEFAULT_YOLO_MODEL)
    parser.add_argument("--library-db", default=DEFAULT_LIBRARY_DB)
    return parser.parse_args()


def load_cover_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_cover_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def google_books_cover(title: str | None, isbns: list[str]) -> dict[str, Any]:
    cache = load_cover_cache()
    key = "|".join(isbns) if isbns else f"title:{title or ''}"
    if key in cache:
        return cache[key]

    queries = []
    for isbn in isbns:
        queries.append(f"isbn:{isbn}")
    if title:
        queries.append(f'intitle:"{title}"')

    result: dict[str, Any] = {"thumbnail": None, "info_link": None, "title": None}
    for query in queries:
        params = urllib.parse.urlencode(
            {"q": query, "maxResults": 3, "printType": "books", "langRestrict": "ja"}
        )
        url = f"https://www.googleapis.com/books/v1/volumes?{params}"
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            result = {"thumbnail": None, "info_link": None, "title": None, "error": str(exc)}
            continue

        for item in data.get("items", []):
            info = item.get("volumeInfo", {})
            image_links = info.get("imageLinks") or {}
            thumbnail = image_links.get("thumbnail") or image_links.get("smallThumbnail")
            if thumbnail:
                result = {
                    "thumbnail": thumbnail.replace("http://", "https://"),
                    "info_link": info.get("infoLink"),
                    "title": info.get("title"),
                }
                cache[key] = result
                save_cover_cache(cache)
                time.sleep(0.1)
                return result

    cache[key] = result
    save_cover_cache(cache)
    time.sleep(0.1)
    return result


def top_candidate(lookup: dict[str, Any] | None) -> dict[str, Any]:
    if not lookup:
        return {}
    candidates = lookup.get("candidates") or []
    return candidates[0] if candidates else {}


def process_image(
    image_path: Path,
    model_path: Path,
    library_db: Path,
    device: str,
    conf: float,
    imgsz: int,
) -> dict[str, Any]:
    run_dir = DEMO_DIR / time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    boxes = detect_and_crop(
        model_path=model_path,
        images=[image_path],
        output_dir=run_dir,
        imgsz=imgsz,
        conf=conf,
        device=device,
        crop_pad=0.02,
    )

    entries = []
    for box in boxes:
        books = gemini_ocr(box.crop_path, "gemini-3.1-flash-lite-preview")
        book_rows = []
        for book in books:
            lookup = library_db_lookup(book.get("title"), library_db)
            candidate = top_candidate(lookup)
            isbns = candidate.get("isbns") or []
            cover = google_books_cover(candidate.get("title") or book.get("title"), isbns)
            book_rows.append(
                {
                    "ocr_title": book.get("title"),
                    "match": candidate,
                    "cover": cover,
                }
            )
        entries.append(
            {
                "box_id": box.box_id,
                "crop": box.crop_path,
                "detector_confidence": box.confidence,
                "books": book_rows,
            }
        )

    preview_path = run_dir / "previews" / image_path.name
    result = {
        "input": image_path,
        "run_dir": run_dir,
        "preview": preview_path if preview_path.exists() else None,
        "entries": entries,
    }
    (run_dir / "result.json").write_text(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    return value


def rel(path: Path | None) -> str:
    if path is None:
        return ""
    return "/" + urllib.parse.quote(str(path.relative_to(REPO_ROOT)))


def page_shell(body: str) -> bytes:
    doc = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Booksearch Demo</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #1d252c;
      --muted: #66717d;
      --line: #d9e0e7;
      --accent: #1f7a5c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 28px 32px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    .upload {{
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    input[type=file] {{ flex: 1; }}
    button {{
      border: 0;
      border-radius: 7px;
      background: var(--accent);
      color: white;
      padding: 10px 16px;
      font-size: 14px;
      font-weight: 650;
      cursor: pointer;
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .metric strong {{ display: block; font-size: 24px; }}
    .preview {{
      width: 100%;
      max-height: 520px;
      object-fit: contain;
      background: #e9edf1;
      border: 1px solid var(--line);
      border-radius: 8px;
      margin: 12px 0 22px;
    }}
    .box {{
      display: grid;
      grid-template-columns: minmax(220px, 320px) 1fr;
      gap: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
    }}
    .crop {{
      width: 100%;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #eef2f4;
    }}
    .box h2 {{ margin: 0 0 10px; font-size: 17px; }}
    .books {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
      gap: 10px;
    }}
    .book {{
      display: grid;
      grid-template-columns: 54px 1fr;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      min-height: 92px;
    }}
    .cover {{
      width: 54px;
      height: 76px;
      object-fit: cover;
      border-radius: 4px;
      background: #dfe5ea;
      border: 1px solid var(--line);
    }}
    .no-cover {{
      width: 54px;
      height: 76px;
      display: grid;
      place-items: center;
      color: var(--muted);
      background: #eef2f4;
      border-radius: 4px;
      font-size: 11px;
      border: 1px solid var(--line);
    }}
    .title {{ font-size: 13px; font-weight: 700; line-height: 1.35; }}
    .meta {{ color: var(--muted); font-size: 12px; line-height: 1.45; margin-top: 4px; }}
    .ocr {{ color: #8a5a00; font-size: 11px; margin-top: 5px; }}
    @media (max-width: 760px) {{
      header {{ padding: 22px 18px; }}
      main {{ padding: 14px; }}
      .upload {{ display: block; }}
      button {{ margin-top: 12px; width: 100%; }}
      .summary {{ grid-template-columns: 1fr; }}
      .box {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<header>
  <h1>Booksearch Demo</h1>
  <p>画像を投げると、箱を切り出してOCRし、校内図書DBで照合して、Google Booksの書影を並べます。</p>
</header>
<main>{body}</main>
</body>
</html>"""
    return doc.encode("utf-8")


def upload_form(message: str = "") -> bytes:
    note = f"<p>{html.escape(message)}</p>" if message else ""
    body = f"""
    <form class="upload" method="post" enctype="multipart/form-data" action="/process">
      <input type="file" name="image" accept="image/*" required>
      <button type="submit">解析する</button>
    </form>
    {note}
    """
    return page_shell(body)


def render_result(result: dict[str, Any]) -> bytes:
    entries = result["entries"]
    book_count = sum(len(entry["books"]) for entry in entries)
    matched_count = sum(1 for entry in entries for book in entry["books"] if book.get("match"))
    preview = f'<img class="preview" src="{rel(result["preview"])}" alt="box preview">' if result.get("preview") else ""
    sections = []
    for entry in entries:
        books_html = []
        for book in entry["books"]:
            match = book.get("match") or {}
            cover = book.get("cover") or {}
            cover_html = (
                f'<img class="cover" src="{html.escape(cover["thumbnail"])}" alt="">'
                if cover.get("thumbnail")
                else '<div class="no-cover">No<br>cover</div>'
            )
            title = match.get("title") or book.get("ocr_title") or ""
            authors = "、".join(match.get("authors") or [])
            publisher = match.get("publisher") or ""
            isbn = " / ".join(match.get("isbns") or [])
            score = match.get("score")
            score_text = f"score {score:.2f}" if isinstance(score, (int, float)) else "no match"
            books_html.append(
                f"""
                <div class="book">
                  {cover_html}
                  <div>
                    <div class="title">{html.escape(title)}</div>
                    <div class="meta">{html.escape(authors)}{(' / ' + html.escape(publisher)) if publisher else ''}</div>
                    <div class="meta">{html.escape(isbn)}</div>
                    <div class="ocr">OCR: {html.escape(book.get("ocr_title") or "")} / {score_text}</div>
                  </div>
                </div>
                """
            )
        sections.append(
            f"""
            <section class="box">
              <div>
                <h2>{html.escape(entry["box_id"])} · {len(entry["books"])} books</h2>
                <img class="crop" src="{rel(entry["crop"])}" alt="crop">
              </div>
              <div class="books">{''.join(books_html)}</div>
            </section>
            """
        )

    body = f"""
    <form class="upload" method="post" enctype="multipart/form-data" action="/process">
      <input type="file" name="image" accept="image/*" required>
      <button type="submit">別の画像を解析</button>
    </form>
    <div class="summary">
      <div class="metric"><strong>{len(entries)}</strong><span>boxes</span></div>
      <div class="metric"><strong>{book_count}</strong><span>OCR titles</span></div>
      <div class="metric"><strong>{matched_count}</strong><span>library matches</span></div>
    </div>
    {preview}
    {''.join(sections)}
    """
    return page_shell(body)


class DemoHandler(BaseHTTPRequestHandler):
    args: argparse.Namespace

    def do_GET(self) -> None:
        path = urllib.parse.unquote(self.path.split("?", 1)[0])
        if path == "/":
            self.respond(upload_form())
            return

        file_path = (REPO_ROOT / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(REPO_ROOT)) or not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return
        content_type = "image/jpeg" if file_path.suffix.lower() in {".jpg", ".jpeg"} else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def do_POST(self) -> None:
        if self.path != "/process":
            self.send_error(404)
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type"),
            },
        )
        field = form["image"] if "image" in form else None
        if field is None or not getattr(field, "filename", ""):
            self.respond(upload_form("画像ファイルを選択してください。"), status=400)
            return

        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        upload_dir = DEMO_DIR / "uploads"
        upload_dir.mkdir(exist_ok=True)
        suffix = Path(field.filename).suffix.lower() or ".jpg"
        image_path = upload_dir / f"upload_{int(time.time())}{suffix}"
        image_path.write_bytes(field.file.read())

        try:
            result = process_image(
                image_path=image_path,
                model_path=resolve_path(self.args.model),
                library_db=resolve_path(self.args.library_db),
                device=self.args.device,
                conf=self.args.conf,
                imgsz=self.args.imgsz,
            )
        except Exception as exc:
            self.respond(upload_form(f"処理に失敗しました: {exc}"), status=500)
            return

        self.respond(render_result(result))

    def respond(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> int:
    args = parse_args()
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY が未設定です。zshrcをsourceしてから起動してください。")
    DemoHandler.args = args
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"Demo UI: http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop.")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())