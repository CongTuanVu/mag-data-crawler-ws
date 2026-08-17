"""Bước 2 — Crawl raw về output_raw/<building_id>/.

Deterministic, không gọi LLM. Kế thừa nguyên tắc raw append-only của WS1:
crawl lại mặc định GỘP THÊM (bỏ URL trùng, đánh số tiếp), dùng --fresh để làm lại.

    pages/<NN>_<slug>.html|.pdf   bản gốc đã render/tải
    pages/<NN>_<slug>.txt         text sạch (nguồn cho extractor)
    pages/<NN>_<slug>.png         screenshot full-page
    floorplans/*.png|.jpg         ảnh mặt bằng căn & mặt bằng tầng (nguồn cho B3)
    manifest.json · crawl_log.csv

Tôn trọng robots.txt (feature_spec §13.8): URL bị chặn được bỏ qua và ghi
status = robots_blocked trong crawl_log.
"""
from __future__ import annotations

import csv
import json
import re
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import pymupdf
from bs4 import BeautifulSoup
from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Từ khoá chỉ để ƯU TIÊN tải ảnh, KHÔNG phải để quyết định ảnh nào là mặt bằng —
# việc đó do bước vision (`is_floorplan`) quyết. Ảnh lớn trên trang mà agent đã gán
# purpose liên quan cũng được tải kể cả không khớp từ khoá nào.
FLOORPLAN_HINTS = ["floorplan", "floor-plan", "floor_plan", "floorplate", "unitplan", "unit-plan",
                   "typical-floor", "siteplan", "layout", "madori", "plan",
                   "matbang", "mat-bang", "mặt bằng",
                   "平面", "户型", "戶型", "間取", "间取", "평면", "배치도", "타입"]
IMAGE_HARVEST_PURPOSES = {"floorplan", "brochure_pdf", "product_mix", "official_overview"}
MAX_PDF_PAGES = 24


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_url(url: str) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urlunparse(p._replace(query=urlencode(q), fragment=""))


def is_pdf(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return "\n".join(ln.strip() for ln in soup.get_text("\n").splitlines() if ln.strip())


def page_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return (soup.title.string or "").strip() if soup.title else ""


class Robots:
    """Cache robots.txt theo host, tải bằng UA trình duyệt qua chính context Playwright.

    Phải dùng UA trình duyệt: nhiều site trả 403 cho urllib, mà RobotFileParser
    hiểu 403 là cấm toàn bộ — sẽ chặn oan những site thực ra cho phép crawl.

    Quy ước trạng thái:
      200          → tuân theo luật trong file
      404/410/4xx  → không có robots.txt, cho phép
      401/403      → site dựng tường chặn bot (Cloudflare…) → KHÔNG crawl.
                     feature_spec §13.8: bị chặn thì chuyển nguồn, không vượt rào.
      lỗi mạng     → cho phép (fail-open), có log
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._cache: Dict[str, Any] = {}

    def _rules(self, host: str):
        if host in self._cache:
            return self._cache[host]
        entry: Any = True                        # True = cho phép tất cả
        try:
            r = self.ctx.request.get(host + "/robots.txt", timeout=20000)
            if r.status == 200:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(r.text().splitlines())
                entry = rp
            elif r.status in (401, 403):
                entry = False                    # tường chặn bot
        except PWError as exc:
            print(f"      · không đọc được robots.txt của {host} ({str(exc)[:60]}) — coi như cho phép")
        self._cache[host] = entry
        return entry

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        rules = self._rules(f"{p.scheme}://{p.netloc}")
        if rules is True:
            return True
        if rules is False:
            return False
        return rules.can_fetch(UA, url)


def _save_pdf_pages(data: bytes, slug: str, fp_dir: Path) -> List[str]:
    """Render trang PDF thành PNG để bước vision đọc mặt bằng."""
    saved: List[str] = []
    doc = pymupdf.open(stream=data, filetype="pdf")
    for i, page in enumerate(doc[:MAX_PDF_PAGES], 1):
        pix = page.get_pixmap(dpi=150)
        name = f"{slug}_p{i:02d}.png"
        pix.save(str(fp_dir / name))
        saved.append(f"floorplans/{name}")
    doc.close()
    return saved


def _harvest_images(ctx, page, base_url: str, slug: str, fp_dir: Path, purpose: str) -> List[str]:
    """Tải ảnh ỨNG VIÊN mặt bằng. Lọc rộng tay: khớp từ khoá, HOẶC ảnh đủ lớn trên
    trang mà agent đã gán purpose liên quan. Ảnh nào thật sự là bản vẽ do bước
    vision quyết, không phải danh sách từ khoá ở đây."""
    try:
        imgs = page.eval_on_selector_all(
            "img",
            "els => els.map(e => ({src: e.currentSrc || e.src, alt: e.alt || '', "
            "cls: e.className || '', w: e.naturalWidth, h: e.naturalHeight}))")
    except PWError:
        return []
    saved: List[str] = []
    for i, im in enumerate(imgs):
        src = (im.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        blob = f"{src} {im.get('alt','')} {im.get('cls','')}".lower()
        w, h = im.get("w") or 0, im.get("h") or 0
        hinted = any(k in blob for k in FLOORPLAN_HINTS)
        big_on_relevant_page = purpose in IMAGE_HARVEST_PURPOSES and w >= 800 and h >= 600
        if not (hinted or big_on_relevant_page):
            continue
        if w < 400 or h < 300:
            continue
        url = urljoin(base_url, src)
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
            ext = ".png"
        try:
            r = ctx.request.get(url, timeout=30000)
            if not r.ok:
                continue
            name = f"{slug}_img{i:02d}{ext}"
            (fp_dir / name).write_bytes(r.body())
            saved.append(f"floorplans/{name}")
        except PWError:
            continue
        if len(saved) >= 12:
            break
    return saved


def _fetch_pdf(ctx, url: str, slug: str, purpose: str, pages_dir: Path, fp_dir: Path,
               e: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    e["kind"] = "pdf"
    try:
        r = ctx.request.get(url, timeout=timeout * 1000)
        e["http_status"] = r.status
        if not r.ok:
            e["error"] = f"HTTP {r.status}"
            print(f"  ✗ PDF HTTP {r.status}")
            return e
        data = r.body()
        (pages_dir / f"{slug}.pdf").write_bytes(data)
        txt = "\n".join(pg.get_text() for pg in pymupdf.open(stream=data, filetype="pdf"))
        (pages_dir / f"{slug}.txt").write_text(txt, encoding="utf-8")
        e.update(chars=len(txt), raw_file=f"pages/{slug}.pdf",
                 text_file=f"pages/{slug}.txt", status="ok")
        # Render mọi PDF: brochure hay bị agent gán purpose khác nhau, và trang nào
        # là bản vẽ thì bước vision quyết — đây chỉ là chuẩn bị ứng viên.
        e["floorplan_files"] = _save_pdf_pages(data, slug, fp_dir)
        print(f"  ✓ PDF {r.status} · {len(txt):,} ký tự · "
              f"{len(e['floorplan_files'])} trang ảnh -> {slug}")
    except PWError as exc:
        e["error"] = str(exc).splitlines()[0][:200]
        print(f"  ✗ PDF {e['error']}")
    except Exception as exc:                       # PDF hỏng / không phải PDF
        e["error"] = f"pdf parse: {str(exc)[:150]}"
        print(f"  ✗ {e['error']}")
    return e


def _crawl_one(ctx, src: Dict[str, Any], idx: int, pages_dir: Path, fp_dir: Path,
               timeout: int, shots: bool) -> Dict[str, Any]:
    url = src["url"]
    p = urlparse(url)
    slug = f"{idx:02d}_" + (config.slugify(src.get("title", ""), "") or
                            config.slugify(f"{p.netloc}_{p.path}"))
    e: Dict[str, Any] = {
        "idx": idx, "title": src.get("title", ""), "purpose": src.get("purpose", ""),
        "expected_content": src.get("expected_content", ""), "url": url, "slug": slug,
        "accessed_at": now_iso(), "kind": "pdf" if is_pdf(url) else "html", "http_status": None,
        "chars": 0, "page_title": "", "raw_file": None, "text_file": None, "shot_file": None,
        "floorplan_files": [], "status": "error", "error": None,
    }
    purpose = src.get("purpose", "")
    if is_pdf(url):
        return _fetch_pdf(ctx, url, slug, purpose, pages_dir, fp_dir, e, timeout)

    page = ctx.new_page()
    try:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        except PWError as exc:
            # URL trả file thay vì trang (brochure không có đuôi .pdf) -> đi nhánh PDF
            if "Download is starting" in str(exc):
                page.close()
                return _fetch_pdf(ctx, url, slug, purpose, pages_dir, fp_dir, e, timeout)
            raise
        e["http_status"] = resp.status if resp else None
        ctype = (resp.header_value("content-type") or "").lower() if resp else ""
        if "application/pdf" in ctype:
            page.close()
            return _fetch_pdf(ctx, url, slug, purpose, pages_dir, fp_dir, e, timeout)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PWTimeout:
            pass
        try:
            for _ in range(5):
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(350)
            page.evaluate("window.scrollTo(0,0)")
            page.wait_for_timeout(500)
        except PWError:
            pass
        html = page.content()
        (pages_dir / f"{slug}.html").write_text(html, encoding="utf-8", errors="ignore")
        txt = visible_text(html)
        (pages_dir / f"{slug}.txt").write_text(txt, encoding="utf-8")
        e.update(chars=len(txt), page_title=page_title(html),
                 raw_file=f"pages/{slug}.html", text_file=f"pages/{slug}.txt")
        e["floorplan_files"] = _harvest_images(ctx, page, url, slug, fp_dir, purpose)
        if shots:
            try:
                page.screenshot(path=str(pages_dir / f"{slug}.png"), full_page=True)
                e["shot_file"] = f"pages/{slug}.png"
            except PWError as exc:
                e["error"] = f"shot: {str(exc).splitlines()[0][:100]}"
        e["status"] = "ok" if (resp and resp.ok) else "http_error"
        print(f"  ✓ {e['http_status']} · {e['chars']:,} ký tự · "
              f"{len(e['floorplan_files'])} ảnh MB -> {slug}")
    except PWTimeout:
        e["error"] = "timeout"
        print("  ✗ timeout")
    except PWError as exc:
        e["error"] = str(exc).splitlines()[0][:200]
        print(f"  ✗ {e['error']}")
    finally:
        page.close()
    return e


def run(building_id: str, sources: List[Dict[str, Any]], out_dir: Path, *,
        fresh: bool = False, timeout: int = 45, shots: bool = True,
        headful: bool = False) -> Dict[str, Any]:
    print(f"[2/4] Crawl raw -> {out_dir}")
    pages_dir, fp_dir = out_dir / "pages", out_dir / "floorplans"
    pages_dir.mkdir(parents=True, exist_ok=True)
    fp_dir.mkdir(parents=True, exist_ok=True)

    man_path = out_dir / "manifest.json"
    existing: List[Dict[str, Any]] = []
    if man_path.exists() and not fresh:
        existing = json.loads(man_path.read_text(encoding="utf-8")).get("sources", [])
    seen = {clean_url(s["url"]) for s in existing}
    start = max([s.get("idx", 0) for s in existing], default=0)

    todo = []
    for s in sources:
        u = clean_url(s["url"])
        if u not in seen:
            seen.add(u)
            todo.append({**s, "url": u})
    print(f"      {len(sources)} nguồn | đã có {len(existing)} | crawl mới {len(todo)}")

    rows: List[Dict[str, Any]] = []
    if todo:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headful)
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900},
                                      locale="en-US", ignore_https_errors=True)
            ctx.set_default_timeout(timeout * 1000)
            robots = Robots(ctx)
            for i, src in enumerate(todo, 1):
                idx = start + i
                print(f"  [{i}/{len(todo)}] (#{idx}) {src.get('purpose','')} <- {src['url'][:90]}")
                if not robots.allowed(src["url"]):
                    print("  ⊘ robots.txt chặn — bỏ qua (spec §13.8)")
                    rows.append({"idx": idx, "url": src["url"], "title": src.get("title", ""),
                                 "purpose": src.get("purpose", ""), "status": "robots_blocked",
                                 "accessed_at": now_iso(), "chars": 0, "floorplan_files": [],
                                 "error": "robots.txt disallow"})
                    continue
                rows.append(_crawl_one(ctx, src, idx, pages_dir, fp_dir, timeout, shots))
            ctx.close()
            browser.close()

    all_rows = existing + rows
    ok = sum(1 for r in all_rows if r.get("status") == "ok")
    n_fp = sum(len(r.get("floorplan_files") or []) for r in all_rows)
    manifest = {"workstream": "ws1_building", "building_id": building_id, "engine": "playwright",
                "accessed_at": now_iso(), "total": len(all_rows), "ok": ok,
                "floorplan_images": n_fp, "sources": all_rows}
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = ["idx", "title", "purpose", "expected_content", "url", "slug", "kind", "status",
              "http_status", "chars", "page_title", "raw_file", "text_file", "shot_file",
              "accessed_at", "error"]
    with (out_dir / "crawl_log.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"      → {len(all_rows)} nguồn, ok={ok}, ảnh mặt bằng={n_fp}")
    return manifest
