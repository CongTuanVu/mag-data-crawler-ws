#!/usr/bin/env python3
"""Trang tổng quan — đếm cả ba nguồn, không diễn giải.

Gọi từ build_market.py; không chạy độc lập.

Ba nguồn nằm ở ba nơi khác nhau và CHƯA ráp vào nhau — trang tổng quan phải nói
rõ điều đó chứ không cộng dồn thành một con số giả:

  1. corpus xuyên quốc gia   parquet  /mnt/data/ws1-data/lanch
  2. bốn bảng Việt Nam       parquet  cùng thư mục, nhưng khác họ bảng
  3. bộ tài liệu             manifest /srv/ws1/data/vinhhd/mdindex

Số của (1) và (2) lấy từ chính parquet, không chép lại CATALOG.md — catalog có
thể cũ hơn dữ liệu. Số của (3) lấy từ `manifest.jsonl`, mỗi dòng một tài liệu.
"""
from __future__ import annotations

import collections
import json
import os

# Tên hiển thị: nguồn thứ ba đặt theo NỘI DUNG nó chứa, không theo tên người tạo.
DOC_LABEL = "Bộ dữ liệu tài liệu"
DOC_MANIFEST = "/srv/ws1/data/vinhhd/mdindex/manifest.jsonl"

ROOT_VI = {"mag": "MAG — nghiên cứu thị trường", "kđt": "KĐT — khu đô thị"}


# ── bản đồ thế giới ─────────────────────────────────────────────────────────
# Đường biên quốc gia lấy từ `world_geo.json` cạnh file này: 177 đa giác đã chiếu
# sẵn plate carrée (lon -180..180 trên x 0..900, lat 83,26..-56,4 trên y 0..360).
# Máy không có geojson/geopandas/gdal và trang phải tự chứa, nên hình học được
# nhúng thẳng — 123 KB.
#
# Hai đa giác Việt Nam và Nhật Bản không mang sẵn mã ISO trong nguồn; xác định lại
# bằng hộp bao, sai số dưới 0,2°: VN 102,2·8,5 → 109,3·23,3 (thật 102,1·8,4 →
# 109,5·23,4). Lào và Campuchia dùng làm đối chứng, cũng khớp.
#
# Singapore và Hong Kong nhỏ hơn một pixel ở tỷ lệ này nên vẽ thành chấm.
ISO = {
    "korea": "KR", "taiwan": "TW", "switzerland": "CH", "netherlands": "NL",
    "usa": "US", "denmark": "DK", "estonia": "EE", "latvia": "LV",
    "france": "FR", "uruguay": "UY", "malaysia": "MY", "russia": "RU",
    "poland": "PL", "kazakhstan": "KZ", "georgia": "GE", "uk": "GB",
    "azerbaijan": "AZ", "moldova": "MD", "singapore": "SG", "hongkong": "HK",
    "vn": "VN", "japan": "JP",
}


def world_map(markets):
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_geo.json")
    if not os.path.exists(src):
        return None
    geo = json.load(open(src, encoding="utf-8"))

    by_iso = {}
    for slug, mk in markets.items():
        code = ISO.get(slug)
        if not code:
            continue
        m = mk["meta"]
        vn = m.get("kind") == "vn"
        by_iso[code] = {
            "slug": slug, "k": m["label"],
            "n": m.get("n_projects") if vn else m.get("n_buildings"),
            "unit": "dự án" if vn else "toà",
            # Việt Nam không có điểm sáu-trường-lõi: nó nằm ở họ bảng khác, chưa
            # ráp vào corpus. Chấm nó 0/6 là nói sai, nên để None và tô riêng.
            "core": None if vn else mk.get("core", {}).get("n_have", 0),
            "strict": mk.get("core", {}).get("n_pass", 0),
            "off": slug == "japan",     # có điểm, nhưng chưa merge vào corpus
        }
    return {"w": geo["w"], "h": geo["h"], "proj": geo.get("proj", ""),
            "features": geo["features"], "dots": geo["dots"],
            "n_countries": len(geo["features"]), "data": by_iso}


def _top(counter, k, other="Còn lại"):
    """top k + phần gộp — cùng cách trang thị trường gom đuôi tỉnh"""
    items = counter.most_common()
    head = items[:k]
    rest = items[k:]
    out = [{"k": a, "n": b} for a, b in head]
    if rest:
        out.append({"k": other, "n": sum(b for _, b in rest), "rest": len(rest)})
    return out


def corpus_stats(con, corpus, markets):
    P = corpus.rstrip("/")
    one = lambda q: con.sql(q).fetchone()[0]

    tables = []
    for f, gran in (("dim_project", "1 dòng = 1 dự án / khu"),
                    ("fact_building", "1 dòng = 1 toà"),
                    ("corpus_loose", "1 dòng = 1 toà, đã nối sẵn"),
                    ("corpus_strict", "1 dòng = 1 toà ĐẠT CHUẨN")):
        path = f"{P}/{f}.parquet"
        if not os.path.exists(path):
            continue
        n = one(f"select count(*) from '{path}'")
        cols = len(con.sql(f"describe select * from '{path}'").fetchall())
        tables.append({"name": f, "n": n, "cols": cols, "gran": gran,
                       "mb": round(os.path.getsize(path) / 1e6, 1)})

    vn = []
    for f, gran in (("vn_project", "1 dòng = 1 dự án"), ("vn_building", "1 dòng = 1 toà"),
                    ("vn_unit", "1 dòng = 1 căn"), ("vn_listing", "1 dòng = 1 tin rao")):
        path = f"{P}/{f}.parquet"
        if not os.path.exists(path):
            continue
        n = one(f"select count(*) from '{path}'")
        cols = len(con.sql(f"describe select * from '{path}'").fetchall())
        vn.append({"name": f, "n": n, "cols": cols, "gran": gran,
                   "mb": round(os.path.getsize(path) / 1e6, 1)})

    # số toà mỗi thị trường, đã GỘP theo đúng nhóm mà trang thị trường dùng
    by_market = [{"k": markets[s]["meta"]["label"],
                  "n": markets[s]["meta"]["n_buildings"],
                  "strict": markets[s].get("core", {}).get("n_pass", 0),
                  "core": markets[s].get("core", {}).get("n_have", 0)}
                 for s in markets
                 if markets[s]["meta"].get("kind") != "vn" and s != "japan"]
    by_market.sort(key=lambda x: -x["n"])

    loose = next((t["n"] for t in tables if t["name"] == "corpus_loose"), 0)
    strict = next((t["n"] for t in tables if t["name"] == "corpus_strict"), 0)
    return {"tables": tables, "vn_tables": vn, "by_market": by_market,
            "loose": loose, "strict": strict,
            "strict_pct": round(100.0 * strict / loose, 1) if loose else 0.0}


def docs_stats(path=DOC_MANIFEST):
    if not os.path.exists(path):
        return None
    n = 0
    total_bytes = 0
    root = collections.Counter()
    job = collections.Counter()
    dom = collections.Counter()
    lang = collections.Counter()
    with_url = with_title = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            n += 1
            total_bytes += r.get("bytes") or 0
            root[r.get("root") or "(chưa rõ)"] += 1
            if r.get("job"):
                job[r["job"]] += 1
            if r.get("domain"):
                dom[r["domain"]] += 1
            # 'zh-Ha' / 'en-US' / 'zh-TW' cùng một tiếng — gộp về mã gốc
            lg = (r.get("lang") or "").split("-")[0].strip().lower()
            if lg:
                lang[lg] += 1
            if r.get("url"):
                with_url += 1
            if r.get("title"):
                with_title += 1
    if not n:
        return None
    return {
        "label": DOC_LABEL, "n_docs": n, "mb": round(total_bytes / 1e6, 1),
        "n_domains": len(dom), "n_langs": len(lang), "n_jobs": len(job),
        "with_url": with_url, "with_title": with_title,
        "roots": [{"k": ROOT_VI.get(k, k), "n": v} for k, v in root.most_common()],
        "domains": _top(dom, 12, "Còn lại"),
        "langs": _top(lang, 10, "Còn lại"),
        "jobs": _top(job, 10, "Còn lại"),
    }


def build_overview(con, corpus, markets):
    ov = {"corpus": corpus_stats(con, corpus, markets), "map": world_map(markets)}
    d = docs_stats()
    if d:
        ov["docs"] = d
    jp = markets.get("japan")
    if jp:
        ov["japan"] = {"n_buildings": jp["meta"]["n_buildings"],
                       "n_projects": jp["meta"]["n_projects"],
                       "n_rows": jp["meta"].get("n_rows", jp["meta"]["n_buildings"])}
    vn = markets.get("vn")
    if vn:
        ov["vn_tiers"] = vn["meta"]["tiers"]
        ov["vn_provinces"] = len(vn.get("by_province") or [])
    return ov
