"""Truy vấn thật trên parquet.

Hai quyết định về tốc độ, đo chứ không đoán:

1. KHÔNG nạp parquet vào bảng bộ nhớ. Đã thử: tốn 912 MiB, truy vấn điểm nhanh
   hơn 13 ms nhưng truy vấn gộp lại CHẬM hơn (19,2 so với 15,6 ms). Parquet đã là
   định dạng cột có zone map nên quét đã rẻ sẵn.

2. Gộp nhiều lượt quét thành MỘT. Bản đầu tính `/markets` bằng cách lặp 20 thị
   trường × 13 truy vấn = ~260 lượt quét 618k dòng. Giờ là một truy vấn duy nhất
   `group by market` với `count(*) filter (...)`, và `/metrics` là hai lượt cho
   cả sáu chỉ tiêu thay vì hơn một trăm.

Quy tắc an toàn: mọi giá trị người dùng gửi lên đều BIND bằng `?`. Chỗ duy nhất
ghép chuỗi là tên cột và hướng sắp xếp, lấy từ danh sách trắng cố định.
"""
from __future__ import annotations

import os
import time

from . import config, db
from . import corpus_gate as G
from .corpus_gate import CORE6, COV_FIELDS, COV_MIN

LOOSE = lambda: f"read_parquet('{config.corpus('corpus_loose')}')"
LATIN = lambda: f"read_parquet('{config.corpus('dim_name_latin')}')"


def latin_markets() -> set[str]:
    """Thị trường nào thật sự có tên cần chuyển tự.

    `dim_name_latin` chỉ có ba hệ chữ: zh-Hant (240.984), ko-Hang (179.149),
    ru-Cyrl (20.360). Nối bảng này cho Anh hay Thuỵ Sĩ là tốn ~200 ms để không
    tìm được gì. Đo một lần lúc khởi động rồi nhớ theo mtime của parquet.
    """
    def scan():
        rows = db.q(f"""select b.market, count(nl.text_latin) as n
                        from {LOOSE()} b join {LATIN()} nl
                          on nl.text_raw = b.building_name
                        group by 1 having count(nl.text_latin) > 0""")
        merged = {c: g for g, cs in config.MARKET_GROUPS.items() for c in cs}
        return {merged.get(r["market"], r["market"]) for r in rows}
    return cached("latin", scan)


def _latin_table() -> dict[str, tuple]:
    """Bảng chuyển tự nạp hẳn vào bộ nhớ: 440.493 mục, ~104 MB, nạp 0,8 giây.

    Đây là ngoại lệ CÓ LÝ DO của nguyên tắc "không nạp parquet vào bộ nhớ" ghi ở
    đầu file. Chỗ kia là bảng dữ liệu 618k dòng mà quét vốn đã rẻ; chỗ này là
    bảng TRA, bị hỏi 100 lần cho mỗi trang và DuckDB không có chỉ mục cho nó.
    Đã đo cả ba cách trên Hàn Quốc:

        nối trước khi cắt trang     355 ms
        nối sau, hai lượt           ~200 ms
        tra bằng danh sách IN       ~250 ms   (quét lại file 5,5 MB mỗi lượt)
        tra trong bộ nhớ            0,005 ms  ← 50.000 lượt hết 4,8 ms
    """
    def load():
        return {r["text_raw"]: (r["text_latin"], r["lang"], r["latin_basis"])
                for r in db.q(f"select text_raw, text_latin, lang, latin_basis "
                              f"from {LATIN()}")}
    return cached("latin_tbl", load)


def romanize(rows: list[dict]) -> list[dict]:
    """Gắn tên Latin cho tên riêng chữ Hàn/Hoa/Nga, SAU khi đã cắt trang.

    Tra trong bảng nhớ — xem `_latin_table()` giải thích vì sao.

    Độ phủ Hàn Quốc: tên toà 125.361/125.373 (100%), chủ đầu tư 96,9%.
    """
    tbl = _latin_table()
    # Không chỉ tên toà và chủ đầu tư: địa bàn và địa chỉ cũng là chữ bản địa, và
    # kho phủ 100% cả hai cho Hàn Quốc bằng `admin_table` / `address_parse` với
    # nền `measured@official_romanization` — bỏ qua là để nguyên chữ Hàn trên trang.
    # Trường nào đòi `measured`: ĐỊA CHỈ. Đã đo trên Hàn Quốc — 123.954 dòng
    # `measured@official_romanization` sạch 100%, còn 189 dòng `derived` thì hỏng
    # 100% ("2-2, -dong, Yeon No., Buk -gu, Hap -teukbyeolsi..."). Với TÊN thì
    # ngược lại: 125.361 tên toà đều là `derived` qua `rr2000` và vẫn đọc tốt,
    # chặn `derived` ở đó là xoá sạch chuyển tự của cả thị trường.
    FIELDS = [("building_name", "name_latin", False), ("developer", "dev_latin", False),
              ("admin", "admin_latin", True), ("address", "addr_latin", True),
              ("project_name", "proj_latin", False)]
    for r in rows:
        for src, dst, need_measured in FIELDS:
            hit = tbl.get(r.get(src))
            ok = hit and (not need_measured
                          or str(hit[2] or "").split("@")[0] == "measured")
            r[dst] = hit[0] if ok else None
        n = tbl.get(r.get("building_name"))
        r["name_lang"] = n[1] if n else None
    return rows

BLD_COLS = [
    "building_name", "project_name", "admin", "address", "developer",
    "n_floors", "n_units_building", "area_m2", "area_kind", "site_area_m2",
    "price", "price_unit", "price_kind", "price_basis", "year_completed",
    "mix", "mix_kind", "building_form", "style", "handover", "amenities",
    "lat", "lon", "n_buildings", "building_code", "market",
]

# Cột mà cổng strict cần nhưng người dùng không xem — lấy vào truy vấn trong rồi
# loại ra ở ngoài, nếu không `_strict` sẽ không bind được cột.
GATE_COLS = ["amenities_basis", "mix_basis", "area_basis", "id_kind",
             "building_level", "scraped_at", "sources"]

SORTS = {
    "full": "_core desc, _strict desc, coalesce(n_units_building,0) desc",
    "units": "coalesce(n_units_building,0) desc",
    "floors": "coalesce(n_floors,0) desc",
    "year": "coalesce(year_completed,0) desc",
    "area": "coalesce(area_m2,0) desc",
    "price": "coalesce(price,0) desc",
    "name": "building_name asc",
}

# key, nhãn, đơn vị, biểu thức, trường quyết định độ phủ
METRICS = [
    ("floors", "Số tầng", "tầng", "n_floors", "n_floors"),
    ("units", "Số căn mỗi toà", "căn", "n_units_building", "n_units_building"),
    ("area", "Diện tích căn", "m²", "area_m2", "area_m2"),
    ("price", "Giá", "", "price", "price"),
    ("site", "Diện tích lô", "m²", "site_area_m2", "site_area_m2"),
    ("dens", "Mật độ căn", "căn/ha",
     "n_units_building/(site_area_m2/10000)", "site_area_m2"),
    ("year", "Năm hoàn thành", "", "year_completed", "year_completed"),
]
# Bảy mốc trong MỘT lượt quét: năm mốc đầu-cuối dùng làm MÉP KHOẢNG của biểu đồ,
# còn 0,25 và 0,75 chỉ để hiện p25/p75 ở dòng tóm tắt. Lỡ dùng cả bảy làm mép thì
# ra 8 cột thay vì 6, lệch hẳn với bản dựng sẵn.
QS = [0.10, 0.25, 0.30, 0.50, 0.70, 0.75, 0.90]
CUT_IDX = [0, 2, 3, 4, 6]        # 0,10 · 0,30 · 0,50 · 0,70 · 0,90


def _detect_types() -> None:
    """Dò kiểu cột một lần rồi báo cho cổng strict biết.

    Parquet đổi `mix` và `amenities` sang kiểu lồng nhau lúc 15:51 ngày
    2026-08-25. Cổng cũ ép chúng về VARCHAR — vẫn ra đúng số nhưng chậm gấp 8 lần
    và chỉ đúng vì tình cờ không có mảng rỗng nào.
    """
    def scan():
        cols = db.q(f"describe select * from {LOOSE()} limit 0")
        lst = {c["column_name"] for c in cols
               if str(c["column_type"]).rstrip().endswith("]")}
        G.set_list_cols(lst)
        return lst
    return cached("types", scan)


def CORE_COND() -> dict:
    _detect_types()
    return G.core_cond()


def STRICT() -> str:
    _detect_types()
    return G.strict_sql()


def _mtime() -> float:
    """Khoá cache: parquet đổi thì mọi số dẫn xuất phải tính lại."""
    try:
        return os.path.getmtime(config.corpus("corpus_loose"))
    except OSError:
        return 0.0


_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, fn):
    """Cache theo mtime của parquet — không TTL, không cần dọn tay."""
    m = _mtime()
    hit = _cache.get(key)
    if hit and hit[0] == m:
        return hit[1]
    val = fn()
    _cache[key] = (m, val)
    return val


def codes_of(slug: str) -> list[str]:
    return config.MARKET_GROUPS.get(slug, [slug])


def _in(codes: list[str]) -> tuple[str, list]:
    return "market in (" + ",".join("?" * len(codes)) + ")", list(codes)


# ── một lượt quét cho TOÀN BỘ thị trường ────────────────────────────────────

def _scan_all() -> dict[str, dict]:
    """MỘT truy vấn dựng xong meta + sáu trường lõi + độ phủ cho mọi thị trường.

    Trước đây chỗ này là ~260 lượt quét 618k dòng. `count(*) filter (...)` cho
    phép đếm mọi điều kiện trong cùng một lượt.
    """
    cc = CORE_COND()
    core_cols = ", ".join(
        f"count(*) filter (where {cc[f]}) as core_{f}" for f, _ in CORE6)
    cov_cols = ", ".join(f"count({f}) as cov_{f}" for f, _ in COV_FIELDS)
    rows = db.q(f"""
        select market, count(*) as n,
               {core_cols}, {cov_cols},
               count(*) filter (where {STRICT()}) as n_strict,
               count(*) filter (where id_kind = 'official_registry') as n_reg,
               any_value(id_kind) as id_kind,
               any_value(price_unit) filter (where price_unit is not null) as price_unit
        from {LOOSE()} group by 1""")

    merged = {c: g for g, cs in config.MARKET_GROUPS.items() for c in cs}
    acc: dict[str, dict] = {}
    for r in rows:
        slug = merged.get(r["market"], r["market"])
        a = acc.setdefault(slug, {"slug": slug, "n": 0, "n_strict": 0, "n_reg": 0,
                                  "core": {}, "cov": {}, "id_kind": None,
                                  "price_unit": None})
        a["n"] += r["n"]
        a["n_strict"] += r["n_strict"]
        a["n_reg"] += r["n_reg"]
        a["id_kind"] = a["id_kind"] or r["id_kind"]
        a["price_unit"] = a["price_unit"] or r["price_unit"]
        for f, _ in CORE6:
            a["core"][f] = a["core"].get(f, 0) + r[f"core_{f}"]
        for f, _ in COV_FIELDS:
            a["cov"][f] = a["cov"].get(f, 0) + r[f"cov_{f}"]
    return acc


def all_markets() -> dict[str, dict]:
    return cached("all", _scan_all)


def _shape(a: dict) -> dict:
    n = a["n"]
    fields = [{"field": f, "label": lb, "pct": round(100.0 * a["core"][f] / n, 1)}
              for f, lb in CORE6]
    return {
        "meta": {"market": a["slug"],
                 "label": config.MARKET_VI.get(a["slug"], a["slug"]),
                 "n_buildings": n, "id_kind": a["id_kind"],
                 "price_unit": a["price_unit"]},
        "core": {"fields": fields, "n_pass": a["n_strict"],
                 "pct": round(100.0 * a["n_strict"] / n, 1),
                 "registry_pct": round(100.0 * a["n_reg"] / n, 1),
                 "n_have": sum(1 for x in fields if x["pct"] >= COV_MIN)},
        "coverage": [{"field": f, "label": lb,
                      "pct": round(100.0 * a["cov"][f] / n, 1)}
                     for f, lb in COV_FIELDS],
    }


def markets() -> list[dict]:
    out = [{**_shape(a)["meta"], "core": _shape(a)["core"]}
           for a in all_markets().values()]
    out.sort(key=lambda m: (-m["core"]["n_have"], -m["core"]["pct"],
                            -m["n_buildings"]))
    return out


def market_detail(slug: str) -> dict | None:
    a = all_markets().get(slug)
    if not a:
        return None
    d = _shape(a)
    d["meta"]["price_basis"] = cached(f"pb:{slug}", lambda: _price_basis(slug))
    d["meta"]["price_units"] = price_units(slug)
    d["forms"] = cached(f"fm:{slug}", lambda: _forms(slug))
    return d


def _price_basis(slug: str) -> list[dict]:
    w, p = _in(codes_of(slug))
    return db.q(f"select price_basis as code, count(*) as n from {LOOSE()} "
                f"where {w} and price_basis is not null group by 1 order by n desc", p)


def price_units(slug: str) -> list[dict]:
    """Đơn vị giá của thị trường, kèm số dòng.

    Hàn Quốc TRỘN hai đơn vị trong cùng cột `price`: 117.966 dòng KRW tuyệt đối
    (trung vị 230 triệu) và 2.049 dòng 만원/m² (trung vị 332) — lệch sáu bậc.
    Gộp chung mà tính phân vị thì biểu đồ giá thực chất chỉ là của nhóm lớn, còn
    toà thuộc nhóm nhỏ rơi vào cột thấp nhất: đúng về số, vô nghĩa về nghĩa.
    """
    w, p = _in(codes_of(slug))
    return cached(f"pu:{slug}", lambda: db.q(
        f"select price_unit as code, count(*) as n from {LOOSE()} "
        f"where {w} and price is not null and price_unit is not null "
        f"group by 1 order by n desc", p))


def _forms(slug: str) -> list[dict]:
    w, p = _in(codes_of(slug))
    a = all_markets()[slug]
    col = "building_form" if a["cov"].get("building_form", 0) else "style"
    return db.q(f"select {col} as code, count(*) as n from {LOOSE()} where {w} "
                f"and {col} is not null group by 1 order by n desc limit 60", p)


# ── duyệt toà ───────────────────────────────────────────────────────────────

def buildings(slug: str, q: str | None, form: str | None, sort: str,
              limit: int, offset: int) -> dict:
    if slug not in all_markets():
        return {"total": 0, "limit": limit, "offset": offset, "rows": []}
    w, params = _in(codes_of(slug))
    where = [w]
    if q:
        # `q` là chữ NGUYÊN VĂN người dùng gõ, giữ nguyên, chỉ bind chứ không ghép.
        where.append("(building_name ilike ? or project_name ilike ? "
                     "or admin ilike ? or developer ilike ?)")
        params += [f"%{q}%"] * 4
    if form:
        where.append("(building_form = ? or style = ?)")
        params += [form, form]
    cond = " and ".join(where)

    cc = CORE_COND()
    core6 = " + ".join(f"case when {cc[f]} then 1 else 0 end" for f, _ in CORE6)
    strict = f"case when {STRICT()} then 1 else 0 end"
    cols = ", ".join(BLD_COLS)
    order = SORTS.get(sort, SORTS["full"])

    # `_core` và `_strict` là cả cổng strict — 16 vị từ trên mỗi dòng. Chỉ
    # `sort=full` mới cần chúng để SẮP XẾP, nên chỉ khi đó mới tính cho toàn bộ
    # dòng. Các kiểu khác sắp xếp bằng cột thường rồi mới tính cổng cho đúng 50
    # dòng trả về. Đo trên Hàn Quốc 125k: 199 ms xuống còn vài chục.
    if sort == "full" or sort not in SORTS:
        sub = (f"(select {cols}, ({core6}) as _core, {strict} as _strict "
               f"from {LOOSE()} where {cond})")
        page = f"select * from {sub} order by {order} limit ? offset ?"
        # `cols` không chứa GATE_COLS nên không cần loại gì thêm ở nhánh này
    else:
        inner = ", ".join(BLD_COLS + GATE_COLS)
        drop = ", ".join(GATE_COLS)
        page = (f"select * exclude ({drop}), ({core6}) as _core, "
                f"{strict} as _strict from "
                f"(select {inner} from {LOOSE()} where {cond} "
                f"order by {order} limit ? offset ?)")

    # Tổng: khi KHÔNG lọc thì lấy thẳng từ lượt quét gộp, không tốn gì. Chỉ khi
    # có `q`/`form` mới phải đếm, và kết quả đếm đó được cache theo bộ lọc — người
    # dùng lật trang thì tổng không đổi, đếm lại mỗi lần là phí một lượt quét.
    #
    # Đã thử `count(*) over ()` để gộp đếm vào cùng truy vấn: CHẬM GẤP ĐÔI
    # (451 so với 217 ms) vì nó buộc vật chất hoá toàn bộ dòng trước khi cắt.
    if not q and not form:
        total = all_markets()[slug]["n"]
    else:
        total = cached(f"cnt:{slug}:{q}:{form}", lambda: db.scalar(
            f"select count(*) from {LOOSE()} where {cond}", params))
    rows = db.q(page, params + [limit, offset])
    # Chỉ tra chuyển tự khi thị trường có chữ không phải Latin.
    if slug in latin_markets():
        rows = romanize(rows)
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


def building(code: str) -> dict | None:
    r = db.one(f"select * from {LOOSE()} where building_code = ? limit 1", [code])
    return romanize([r])[0] if r else None


# ── phân bố ─────────────────────────────────────────────────────────────────

def metrics(slug: str, form: str | None) -> list[dict]:
    """Phân bố tính LẠI theo bộ lọc đang chọn — hai lượt quét cho cả sáu chỉ tiêu.

    Lượt 1 lấy bảy phân vị của mọi chỉ tiêu cùng lúc (`quantile_cont` nhận danh
    sách mốc nên chỉ sắp xếp một lần). Lượt 2 đếm sáu khoảng của mọi chỉ tiêu
    bằng `count(*) filter (...)`. Bản đầu là hơn một trăm lượt.
    """
    a = all_markets().get(slug)
    if not a:
        return []
    w, base = _in(codes_of(slug))
    extra, ep = "", []
    if form:
        extra, ep = " and (building_form = ? or style = ?)", [form, form]
    params = base + ep

    use = [m for m in METRICS
           if 100.0 * a["cov"].get(m[4], 0) / a["n"] >= COV_MIN][:6]
    if not use:
        return []

    # Giá chỉ tính trên ĐƠN VỊ CHIẾM ĐA SỐ. Trộn hai thang trong một biểu đồ là
    # dựng ra một phân bố không tồn tại — xem `price_units()`.
    pu = price_units(slug)
    main_unit = pu[0]["code"] if pu else None
    mixed = len(pu) > 1

    guard = {}
    for m in use:
        g = f"{m[4]} is not null"
        if m[0] == "dens":
            g += " and site_area_m2 > 0 and n_units_building > 0"
        if m[0] == "price" and mixed and main_unit:
            g += f" and price_unit = '{main_unit}'"
        guard[m[0]] = g
    qcols = ", ".join(
        f"quantile_cont({m[3]}, {QS}) filter (where {guard[m[0]]}) as q_{m[0]}, "
        f"count(*) filter (where {guard[m[0]]}) as n_{m[0]}" for m in use)
    agg = db.one(f"select {qcols} from {LOOSE()} where {w}{extra}", params)
    if not agg:
        return []

    out, bin_cols = [], []
    for key, label, unit, expr, _ in use:
        qs = agg[f"q_{key}"]
        n = agg[f"n_{key}"]
        if not n or qs is None:
            continue
        cuts = sorted({round(float(qs[i]), 2) for i in CUT_IDX
                       if qs[i] is not None})
        edges = [None] + cuts + [None] if len(cuts) else []
        spec = []
        for i in range(len(cuts) + 1):
            lo = cuts[i - 1] if i else None
            hi = cuts[i] if i < len(cuts) else None
            c = ([f"{expr} >= {lo}"] if lo is not None else []) + \
                ([f"{expr} < {hi}"] if hi is not None else [])
            spec.append((lo, hi, " and ".join(c) or "1=1"))
            bin_cols.append(f"count(*) filter (where {guard[key]} and {spec[-1][2]}) "
                            f"as b_{key}_{i}")
        e = {"key": key, "label": label, "unit": unit, "n": n,
             "p25": qs[1], "med": qs[3], "p75": qs[5], "_spec": spec}
        if key == "price" and main_unit:
            e["unit"] = main_unit
            if mixed:
                e["note"] = ("Cột giá thị trường này trộn "
                             + " và ".join(f"{x['n']:,} dòng {x['code']}".replace(",", ".")
                                           for x in pu)
                             + f" — lệch thang. Phân bố chỉ tính trên {main_unit}; "
                               "toà thuộc đơn vị khác không so được vào đây.")
        out.append(e)

    if bin_cols:
        counts = db.one(f"select {', '.join(bin_cols)} from {LOOSE()} "
                        f"where {w}{extra}", params) or {}
        for m in out:
            m["bins"] = [{"lo": lo, "hi": hi,
                          "n": counts.get(f"b_{m['key']}_{i}", 0)}
                         for i, (lo, hi, _) in enumerate(m.pop("_spec"))]
    return out
