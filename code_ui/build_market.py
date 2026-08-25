#!/usr/bin/env python3
"""Dựng trang thị trường tự chứa từ corpus parquet.

Mỗi thị trường ra một file HTML mở thẳng bằng file:// — không server, không CDN.
`duckdb` chỉ cần ở bước build; file sinh ra không phụ thuộc gì.

    python3 code_ui/build_market.py
    python3 code_ui/build_market.py --market taiwan --sample 1000

Nhãn hiển thị lấy từ `dim_enum` (mã → cụm từ) và `dim_name_latin` (tên riêng →
chữ Latin). Không hardcode bản dịch nào trong file này.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
CORPUS = "/mnt/data/ws1-data/lanch"

# Nhiều mã thị trường cùng một nước, khác nguồn — gộp lại thành một.
MARKET_GROUPS = {
    "taiwan": ["taiwan", "taiwan_ext", "taiwan_new"],
    "poland": ["poland", "poland_korter"],
}

MARKET_VI = {
    "korea": "Hàn Quốc", "singapore": "Singapore", "taiwan": "Đài Loan",
    "switzerland": "Thuỵ Sĩ", "netherlands": "Hà Lan", "usa": "Hoa Kỳ",
    "denmark": "Đan Mạch", "estonia": "Estonia", "latvia": "Latvia",
    "france": "Pháp", "uruguay": "Uruguay", "malaysia": "Malaysia",
    "russia": "Nga", "poland": "Ba Lan",
    "kazakhstan": "Kazakhstan", "georgia": "Gruzia", "hongkong": "Hong Kong",
    "uk": "Anh", "azerbaijan": "Azerbaijan", "moldova": "Moldova",
}

BLD = [
    "building_name", "project_name", "admin", "address", "developer",
    "n_floors", "n_units_building", "area_m2", "area_kind", "site_area_m2",
    "price", "price_unit", "price_kind", "price_basis", "year_completed",
    "mix", "mix_kind", "building_form", "style", "handover", "amenities",
    "lat", "lon", "n_buildings", "building_code", "sources",
]
CORE = ["n_floors", "n_units_building", "area_m2", "price", "year_completed",
        "developer", "amenities", "building_form", "style", "handover", "mix", "lat", "site_area_m2"]

REQUIRED = [("floors", "Số tầng", "tầng", "n_floors", "n_floors"),
            ("units", "Số căn mỗi toà", "căn", "n_units_building", "n_units_building"),
            ("area", "Diện tích căn", "m²", "area_m2", "area_m2"),
            ("price", "Giá", "", "price", "price")]
FILLERS = [("site", "Diện tích lô", "m²", "site_area_m2", "site_area_m2"),
           ("dens", "Mật độ căn", "căn/ha", "n_units_building/(site_area_m2/10000)", "site_area_m2"),
           ("year", "Năm hoàn thành", "", "year_completed", "year_completed"),
           ("amen", "Số tiện ích", "mục", "len(from_json(amenities,'[\"varchar\"]'))", "amenities")]
N_METRICS, COV_MIN = 6, 50.0

# ── SÁU TRƯỜNG LÕI ──────────────────────────────────────────────────────────
# Định nghĩa của kho, không phải lựa chọn của trang này. Nguồn:
#   similarity_check/SCHEMA-V2.md  §corpus_strict
#   similarity_check/scripts/build_schema_v2.py  STRICT_SQL
# Đã đối chiếu: lọc corpus_loose bằng cổng dưới đây ra đúng 157.384 dòng,
# khớp tuyệt đối với corpus_strict.parquet.
CORE6 = [("mix", "cơ cấu căn"), ("area_m2", "diện tích căn"), ("price", "giá"),
         ("amenities", "tiện ích"), ("style", "phong cách"), ("handover", "bàn giao")]


def _nz(f):
    return f"{f} IS NOT NULL AND CAST({f} AS VARCHAR) NOT IN ('', '[]', '{{}}')"


def _basis(b):
    return f"split_part(coalesce({b}, ''), '@', 1) IN ('measured', 'verified_none')"


# tiện ích đạt nếu có danh sách, HOẶC rỗng nhưng nguồn khai verified_none
AMEN_OK = f"(({_nz('amenities')}) OR (amenities = '[]' AND amenities_basis = 'verified_none'))"

CORE_COND = {f: (AMEN_OK if f == "amenities" else _nz(f)) for f, _ in CORE6}

# mức bằng chứng CHỈ áp cho bốn trường; style/handover được nới có chủ đích
STRICT_SQL = " AND ".join([
    _nz("mix"), "id_kind = 'official_registry'",
    "building_level IN ('building', 'derived_single')",
    _nz("area_m2"), _nz("price"), _nz("price_kind"),
    _nz("style"), _nz("handover"), _nz("sources"),
    _nz("scraped_at"), _nz("building_name"), AMEN_OK,
    _basis("mix_basis"), _basis("area_basis"),
    _basis("price_basis"), _basis("amenities_basis"),
])
METRIC_FIELD = {"floors": "b.n_floors", "units": "b.n_units_building", "area": "b.area_m2",
                "price": "b.price", "site": "b.site_area_m2", "year": "b.year_completed",
                "amen": "b.amenities",
                "dens": "case when b.site_area_m2 > 0 and b.n_units_building > 0 then 1 end"}

COV_FIELDS = [("n_floors", "số tầng"), ("n_units_building", "số căn"),
              ("area_m2", "diện tích căn"), ("price", "giá"),
              ("site_area_m2", "diện tích lô"), ("lat", "toạ độ"),
              ("mix", "cơ cấu căn"), ("year_completed", "năm hoàn thành"),
              ("amenities", "tiện ích"), ("building_form", "loại hình"), ("style", "phong cách")]


def _band_range(key):
    """`le60` → (0,60) · `m60_85` → (60,85) · `gt135` → (135,inf) · `0-40` → (0,40)"""
    s = str(key).strip()
    m = re.match(r"^le(\d+)$", s) or re.match(r"^(?:<=|≤)\s*(\d+)$", s) or re.match(r"^<\s*(\d+)$", s)
    if m:
        return (0.0, float(m.group(1)))
    m = re.match(r"^gt(\d+)$", s) or re.match(r"^(?:>=|≥)\s*(\d+)$", s) or re.match(r"^(\d+)\+$", s) or re.match(r"^>\s*(\d+)$", s)
    if m:
        return (float(m.group(1)), float("inf"))
    m = re.match(r"^m(\d+)_(\d+)$", s) or re.match(r"^(\d+)\s*[-–]\s*(\d+)$", s)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    return None


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def num(x):
    if x is None:
        return None
    f = float(x)
    return int(f) if f == int(f) and abs(f) < 1e15 else round(f, 4)


def edge(v):
    if v is None:
        return ""
    a = abs(v)
    if a >= 1e9:
        return f"{v/1e9:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " tỷ"
    if a >= 1e6:
        return f"{v/1e6:.0f} tr"
    if a >= 1e4:
        return f"{v/1e3:.0f}k"
    if a >= 100:
        return f"{v:,.0f}".replace(",", ".")
    if v == int(v):
        return f"{int(v):,}".replace(",", ".")
    if a >= 10:
        return f"{v:.0f}"
    return f"{v:.1f}".replace(".", ",")


class Market:
    def __init__(self, con, market):
        self.c, self.m = con, market
        self.codes = MARKET_GROUPS.get(market, [market])
        self.S = f"'{CORPUS}/corpus_loose.parquet'"
        inl = "', '".join(self.codes)
        self.W = f"from {self.S} where market in ('{inl}')"
        self.IN = f"market in ('{inl}')"

    def one(self, q):
        return self.c.sql(q).fetchone()

    def coverage(self, tot):
        return [{"field": f, "label": lb,
                 "pct": round(100.0 * self.one(f"select count({f}) {self.W}")[0] / tot, 1)}
                for f, lb in COV_FIELDS]

    def metric(self, key, label, unit, expr, base):
        cond = f"{base} is not null" + (" and site_area_m2 > 0 and n_units_building > 0"
                                        if key == "dens" else "")
        sub = f"(select {expr} x {self.W} and {cond})"
        n, p25, med, p75 = self.one(
            f"select count(*), quantile_cont(x,0.25), median(x), quantile_cont(x,0.75) from {sub}")
        if not n or p25 is None:
            return None
        cuts = sorted({round(self.one(f"select quantile_cont(x,{q}) from {sub}")[0], 2)
                       for q in (0.10, 0.30, 0.50, 0.70, 0.90)
                       if self.one(f"select quantile_cont(x,{q}) from {sub}")[0] is not None})
        bins = []
        for i in range(len(cuts) + 1):
            a = cuts[i - 1] if i else None
            b = cuts[i] if i < len(cuts) else None
            if a is None:
                c2, lab = f"{expr} < {b}", f"< {edge(b)}"
            elif b is None:
                c2, lab = f"{expr} >= {a}", f"≥ {edge(a)}"
            else:
                c2, lab = f"{expr} >= {a} and {expr} < {b}", f"{edge(a)}–{edge(b)}"
            bins.append({"label": lab, "lo": num(a), "hi": num(b),
                         "n": self.one(f"select count(*) {self.W} and {cond} and {c2}")[0]})
        return {"key": key, "label": label, "unit": unit, "n": n,
                "p25": num(p25), "med": num(med), "p75": num(p75), "bins": bins}

    def core(self, tot):
        """Sáu trường lõi của kho, kèm tỷ lệ toà qua được cổng strict."""
        out = []
        for f, label in CORE6:
            n = self.one(f"select count(*) {self.W} and {CORE_COND[f]}")[0]
            out.append({"field": f, "label": label, "pct": round(100.0 * n / tot, 1)})
        st = self.one(f"select count(*) {self.W} and {STRICT_SQL}")[0]
        reg = self.one(f"select count(*) {self.W} and id_kind = 'official_registry'")[0]
        return {"fields": out, "n_pass": st, "pct": round(100.0 * st / tot, 1),
                "registry_pct": round(100.0 * reg / tot, 1),
                "n_have": sum(1 for x in out if x["pct"] >= COV_MIN)}

    def metrics(self, cov):
        pct = {c["field"]: c["pct"] for c in cov}
        out = []
        for key, lb, u, ex, base in REQUIRED:
            if pct.get(base, 0) >= COV_MIN:
                mt = self.metric(key, lb, u, ex, base)
                if mt:
                    out.append(mt)
        for key, lb, u, ex, base in FILLERS:
            if len(out) >= N_METRICS:
                break
            if pct.get(base, 0) >= COV_MIN:
                mt = self.metric(key, lb, u, ex, base)
                if mt:
                    out.append(mt)
        return out[:N_METRICS]

    def buildings(self, k, metric_keys=()):
        full = " + ".join(f"case when b.{f} is null then 0 else 1 end" for f in CORE)
        core6 = " + ".join(f"case when {CORE_COND[f].replace(f, 'b.' + f, 1)} then 1 else 0 end"
                           for f, _ in CORE6)
        strict = STRICT_SQL
        met = " + ".join(f"case when {METRIC_FIELD[k]} is null then 0 else 1 end"
                         for k in metric_keys if k in METRIC_FIELD) or "0"
        cols = ", ".join("b." + f for f in BLD)
        per = max(1, k // 20)
        q = f"""
        with base as (
          select {cols}, ({full}) as _full, ({met}) as _met,
                 ({core6}) as _core, case when {strict} then 1 else 0 end as _strict,
                 nl.text_latin as name_latin, nl.lang as name_lang,
                 dl.text_latin as dev_latin,
                 ntile(20) over (order by coalesce(b.n_units_building,0)) as _t
          from {self.S} b
          left join '{CORPUS}/dim_name_latin.parquet' nl on nl.text_raw = b.building_name
          left join '{CORPUS}/dim_name_latin.parquet' dl on dl.text_raw = b.developer
          where b.{self.IN}
        ), ranked as (
          select *, row_number() over (partition by _t
                     order by _core desc, _strict desc, _met desc,
                              _full desc, n_units_building desc) as _r
          from base
        )
        select * exclude (_t, _r) from ranked where _r <= {per}
        """
        r = self.c.sql(q)
        names = list(r.columns)
        return [dict(zip(names, row)) for row in r.fetchall()]

    def mix_bands(self):
        """Mảng số trần trong `mix` KHÔNG mang tên dải, và kho không khai báo ở đâu
        vị trí nào là dải nào — đã tra dim_enum, schema/*.md, CATALOG.json và cột
        `metadata` (0/2.868 dòng mảng có metadata).

        Nên ở đây suy bộ khoá từ chính các bản ghi dạng dict của thị trường, rồi
        KIỂM bằng `area_m2`: toà nào dồn 100% vào một vị trí thì diện tích bình quân
        của nó phải rơi vào đúng dải đó. Không đạt ngưỡng → trả None, UI quay về
        nhãn trung tính thay vì đặt tên sai trong im lặng.
        """
        n_arr = self.one(f"select count(*) {self.W} and mix is not null "
                         f"and starts_with(mix, '[')")[0]
        if not n_arr:
            return None
        rows = self.c.sql(f"select mix {self.W} and mix is not null "
                          f"and starts_with(mix, '{{') limit 4000").fetchall()
        tally = {}
        for (s,) in rows:
            try:
                k = tuple(json.loads(s).keys())
            except Exception:
                continue
            tally[k] = tally.get(k, 0) + 1
        if not tally:
            return None
        keys = list(max(tally, key=tally.get))

        rng = [_band_range(k) for k in keys]
        if any(r is None for r in rng):
            return None

        probe = self.c.sql(f"select mix, area_m2 {self.W} and mix is not null "
                           f"and starts_with(mix, '[') and area_m2 is not null").fetchall()
        hit = miss = 0
        for s, area in probe:
            try:
                v = json.loads(s)
            except Exception:
                continue
            if len(v) != len(keys):
                continue
            hot = [i for i, x in enumerate(v) if x and x >= 0.999]
            if len(hot) != 1:
                continue
            lo, hi = rng[hot[0]]
            if lo <= area < hi:
                hit += 1
            else:
                miss += 1
        n = hit + miss
        if n < 30:
            return None
        acc = hit / n
        print(f"      mix_bands: {keys} — kiểm {n:,} toà, khớp {acc*100:.1f}%"
              + ("" if acc >= 0.95 else "  → KHÔNG đạt 95%, bỏ nhãn"))
        if acc < 0.95:
            return None
        return {"keys": keys, "checked": n, "accuracy": round(acc, 4)}

    def meta(self):
        nb = self.one(f"select count(*) {self.W}")[0]
        npj = self.one(f"select count(*) from '{CORPUS}/dim_project.parquet' where {self.IN}")[0]
        auth = ", ".join(sorted(
            r[0] for r in self.c.sql(f"select distinct id_authority {self.W} "
                                     f"and id_authority is not null").fetchall()))
        kind = self.one(f"select any_value(id_kind) {self.W}")[0]
        unit = self.one(f"select any_value(price_unit) {self.W} and price_unit is not null")[0]
        pk = self.c.sql(f"select price_basis, count(*) n {self.W} and price_basis is not null "
                        f"group by 1 order by n desc").fetchall()
        return {"market": self.m, "label": MARKET_VI.get(self.m, self.m),
                "n_buildings": nb, "n_projects": npj, "id_authority": auth,
                "id_kind": kind, "price_unit": unit,
                "price_basis": [{"code": a, "n": b} for a, b in pk]}


# hằng số trong phạm vi một thị trường — hoist lên cấp thị trường cho nhẹ file
HOIST = ["price_unit", "sources", "area_kind", "handover", "mix_kind"]


def enum_map(con):
    rows = con.sql(f"""select field, code, label_vi, label_en, definition_vi,
                       sort_order, attrs from '{CORPUS}/dim_enum.parquet'
                       where status = 'active' order by field, sort_order""").fetchall()
    out = {}
    for f, code, v, e, d, o, attrs in rows:
        g = None
        if attrs:
            try:
                g = json.loads(attrs).get("group")
            except Exception:
                pass
        out.setdefault(f, {})[code] = {"vi": v, "en": e, "def": d, "order": o, "group": g}
    return out


def market_list(con):
    raw = [r[0] for r in con.sql(
        f"select market, count(*) n from '{CORPUS}/corpus_loose.parquet' "
        f"group by 1 order by n desc").fetchall()]
    merged = {c: g for g, cs in MARKET_GROUPS.items() for c in cs}
    out = []
    for m in raw:
        key = merged.get(m, m)
        if key not in out:
            out.append(key)
    return out


def compact(rows):
    """bỏ null + hoist giá trị chiếm đa số của các trường gần-hằng-số"""
    defaults = {}
    for k in HOIST:
        tally = {}
        for r in rows:
            v = r.get(k)
            if v is not None:
                tally[str(v)] = tally.get(str(v), 0) + 1
        if tally:
            top = max(tally, key=tally.get)
            if tally[top] / len(rows) >= 0.8:
                defaults[k] = next(r[k] for r in rows if str(r.get(k)) == top)
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            if v is None:
                continue
            if k in defaults and str(v) == str(defaults[k]):
                continue
            d[k] = v
        out.append(d)
    return out, defaults


def build_market(con, market, sample):
    mk = Market(con, market)
    meta = mk.meta()
    if not meta["n_buildings"]:
        return None
    cov = mk.coverage(meta["n_buildings"])
    metrics = mk.metrics(cov)
    rows = mk.buildings(sample, [m["key"] for m in metrics])
    blds, defaults = compact(rows)
    out = {"meta": meta, "coverage": cov, "metrics": metrics,
           "core": mk.core(meta["n_buildings"]),
           "defaults": defaults, "buildings": blds}
    bands = mk.mix_bands()
    if bands:
        out["mix_bands"] = bands
    return out


def set_corpus(path):
    global CORPUS
    CORPUS = path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", action="append", help="chỉ dựng thị trường này")
    ap.add_argument("--sample", type=int, default=250, help="số toà mẫu mỗi thị trường")
    ap.add_argument("--out", default=str(HERE / "dist" / "index.html"))
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--export", action="store_true",
                    help="nhúng toàn bộ dữ liệu vào file (bản tự chứa, mở bằng "
                         "file:// vẫn chạy). Không có cờ này thì trang gọi API.")
    a = ap.parse_args()
    set_corpus(a.corpus.rstrip("/"))

    try:
        import duckdb
    except ImportError:
        sys.exit("cần duckdb ở bước build:  pip install duckdb")

    tpl = HERE / "template_market.html"
    if not tpl.exists():
        sys.exit(f"thiếu {tpl}")

    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='4GB'")

    markets, order = {}, []

    # Việt Nam đi đường riêng: bốn cấp dữ liệu và có toạ độ
    if not a.market or "vn" in a.market:
        try:
            import build_vn as BV
            vn = BV.build_vn(con, CORPUS, a.sample, edge, num)
            projs = BV.vn_projects(con, CORPUS, a.sample)
            ids = [p["entity_id"] for p in projs if p.get("entity_id")]
            vn["projects"] = projs
            vn["map"] = BV.vn_map_path(con, CORPUS)
            vn["by_province"] = BV.vn_by_province(con, CORPUS)
            vn["buildings_of"] = BV.vn_buildings_of(con, CORPUS, ids)
            vn["listings_of"] = BV.vn_listings_of(con, CORPUS, ids)
            markets["vn"] = vn
            order.append("vn")
            m = vn["meta"]
            print(f"  {'Việt Nam':22s} {m['n_projects']:>7,d} dự án → mẫu {len(projs):>4,d}"
                  f" · {m['n_buildings']:,} toà · {m['n_listings']:,} tin rao"
                  f" · {m['n_units']:,} căn"
                  f" · nối lên cha: " + " / ".join(
                      f"{t['label']} {100.0*t['up']/t['n']:.0f}%"
                      for t in m['tiers'] if t.get('up') is not None)
                  + f" · {len(vn['metrics_by_cat'])} bộ phân bố"
                  f" · {len(vn['by_province'])} tỉnh")
        except Exception as e:
            print(f"  ✗ vn: {e}", file=sys.stderr)
    # Nhật Bản: không nằm trong corpus parquet, dựng từ output_csv/ của repo này
    jp_enums = {}
    if not a.market or "japan" in a.market:
        try:
            import build_jp as BJ
            jp, jp_enums = BJ.build_jp(str(ROOT / "output_csv"), edge, num)
            if jp:
                markets["japan"] = jp
                order.append("japan")
                m = jp["meta"]
                print(f"  {m['label']:22s} {m['n_buildings']:>7,d} toà"
                      f" → mẫu {len(jp['buildings']):>4,d} · lõi {jp['core']['n_have']}/6"
                      f" · ngoài corpus     · {len(jp['metrics'])} phân bố")
        except Exception as e:
            print(f"  ✗ japan: {e}", file=sys.stderr)

    for m in [x for x in (a.market or market_list(con)) if x not in ("vn", "japan")]:
        try:
            r = build_market(con, m, a.sample)
        except Exception as e:
            print(f"  ✗ {m}: {e}", file=sys.stderr)
            continue
        if not r:
            continue
        markets[m] = r
        order.append(m)
        print(f"  {r['meta']['label']:22s} {r['meta']['n_buildings']:>7,d} toà"
              f" → mẫu {len(r['buildings']):>4,d} · lõi {r['core']['n_have']}/6"
              f" · strict {r['core']['pct']:>5.1f}% · {len(r['metrics'])} phân bố")

    order.sort(key=lambda s: (0 if s == "vn" else 1,
                              -markets[s].get("core", {}).get("n_have", 0),
                              -markets[s].get("core", {}).get("pct", 0),
                              -markets[s]["meta"].get("n_buildings", 0)))
    try:
        import build_overview as BO
        overview = BO.build_overview(con, CORPUS, markets)
        d = overview.get("docs")
        print(f"\n  tổng quan: {len(overview['corpus']['tables'])} bảng corpus"
              f" · {len(overview['corpus']['vn_tables'])} bảng VN"
              + (f" · {d['label']} {d['n_docs']:,} tài liệu / {d['mb']:,.0f} MB"
                 f" / {d['n_domains']:,} tên miền / {d['n_langs']} tiếng" if d
                 else " · KHÔNG thấy manifest tài liệu"))
    except Exception as e:
        overview = None
        print(f"  ✗ tổng quan: {e}", file=sys.stderr)

    enums = enum_map(con)
    for f, rows in jp_enums.items():          # nhãn riêng của nguồn Nhật, không có trong dim_enum
        enums.setdefault(f, {}).update(rows)
    # Hình đường biên quốc gia là HÌNH HỌC tĩnh, không phải dữ liệu — nó ở lại
    # trong trang ở cả hai chế độ. Phần tô màu theo thị trường thì lấy từ API.
    world = None
    try:
        import build_overview as BO
        geo = json.loads((HERE / "world_geo.json").read_text(encoding="utf-8"))
        world = {"w": geo["w"], "h": geo["h"], "features": geo["features"],
                 "dots": geo["dots"], "n_countries": len(geo["features"]),
                 "iso": BO.ISO}
    except Exception as e:
        print(f"  ✗ hình bản đồ: {e}", file=sys.stderr)

    if a.export:
        payload = {"order": order, "markets": markets, "enums": enums,
                   "overview": overview, "n_core": len(CORE), "corpus": CORPUS,
                   "world": world}
    else:
        # Trang gọi API: chỉ mang nhãn tối thiểu và hình bản đồ. Mọi con số lấy
        # lúc chạy, nên sửa parquet là thấy ngay, không phải dựng lại trang.
        payload = {"n_core": len(CORE), "world": world}
    blob = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
    html = tpl.read_text(encoding="utf-8").replace("__DATA__", blob.replace("</", "<\\/"))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    size = out.stat().st_size
    mode = "tự chứa (nhúng dữ liệu)" if a.export else "gọi API lúc chạy"
    print(f"\n{len(order)} thị trường · {size/1024/1024:.1f} MB · {mode}\n{out}")
    if a.export and size > 12 * 1024 * 1024:
        print("  ⚠ file lớn — cân nhắc giảm --sample", file=sys.stderr)
    if not a.export:
        print("  trang này cần API sống ở /ws1-data/api/ — "
              "`docker compose up -d` ở gốc repo")


if __name__ == "__main__":
    main()
