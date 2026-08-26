"""Định nghĩa SÁU TRƯỜNG LÕI và cổng `corpus_strict`.

⚠️ Đang TRÙNG với `code_ui/build_market.py`. Hai bản phải khớp nhau, nếu lệch thì
số trên trang và số trong API sẽ khác nhau mà không ai biết. Việc cần làm sau:
gộp về một chỗ và cho builder import từ đây.

Nguồn định nghĩa, không phải lựa chọn của file này:
    similarity_check/SCHEMA-V2.md      §corpus_strict
    similarity_check/scripts/build_schema_v2.py   STRICT_SQL

Đã đối chiếu: lọc `corpus_loose` bằng cổng dưới đây ra đúng 157.384 dòng, khớp
tuyệt đối với `corpus_strict.parquet`.
"""
from __future__ import annotations

CORE6 = [
    ("mix", "cơ cấu căn"), ("area_m2", "diện tích căn"), ("price", "giá"),
    ("amenities", "tiện ích"), ("style", "phong cách"), ("handover", "bàn giao"),
]

COV_FIELDS = [
    ("n_floors", "số tầng"), ("n_units_building", "số căn"),
    ("area_m2", "diện tích căn"), ("price", "giá"),
    ("site_area_m2", "diện tích lô"), ("lat", "toạ độ"),
    ("mix", "cơ cấu căn"), ("year_completed", "năm hoàn thành"),
    ("amenities", "tiện ích"), ("building_form", "loại hình"), ("style", "phong cách"),
]

COV_MIN = 50.0


# Từ 2026-08-25 15:51, `mix` và `amenities` trong parquet là KIỂU LỒNG NHAU thật
# (`STRUCT(...)[]` và `VARCHAR[]`), không còn là chuỗi JSON. Ép chúng về VARCHAR
# để so với '[]' vẫn ra đúng kết quả nhưng CHẬM GẤP 8 LẦN — đo trên `mix`:
# 311 ms so với 40 ms, cùng ra 460.271 dòng. Và nó chỉ đúng nhờ may: hiện không
# có mảng rỗng nào, toàn NULL; có mảng rỗng thì phép so chuỗi sẽ đếm nhầm.
#
# Nên chọn vị từ THEO KIỂU CỘT, dò một lần lúc khởi động.
LIST_COLS: set[str] = set()


def set_list_cols(cols) -> None:
    LIST_COLS.clear()
    LIST_COLS.update(cols)


def nz(f: str) -> str:
    """Trường có giá trị thật — chuỗi rỗng, mảng rỗng đều tính là khuyết."""
    base = f.split(".")[-1]
    if base in LIST_COLS:
        return f"{f} IS NOT NULL AND len({f}) > 0"
    return f"{f} IS NOT NULL AND CAST({f} AS VARCHAR) NOT IN ('', '[]', '{{}}')"


def basis_ok(b: str) -> str:
    """Mức bằng chứng; hậu tố `@` là biến thể đo được nên cắt bỏ trước khi so."""
    return f"split_part(coalesce({b}, ''), '@', 1) IN ('measured', 'verified_none')"


def amen_ok() -> str:
    """Tiện ích đạt nếu CÓ danh sách, hoặc RỖNG nhưng nguồn xác nhận là không có.

    Nhánh thứ hai phải so đúng kiểu: với `VARCHAR[]` là `len(...) = 0`, không
    phải `= '[]'` — phép so chuỗi kia âm thầm không khớp gì cả.
    """
    empty = ("len(amenities) = 0" if "amenities" in LIST_COLS
             else "amenities = '[]'")
    return (f"(({nz('amenities')}) OR (amenities IS NOT NULL AND {empty} "
            f"AND amenities_basis = 'verified_none'))")


def core_cond() -> dict:
    a = amen_ok()
    return {f: (a if f == "amenities" else nz(f)) for f, _ in CORE6}

# Mức bằng chứng CHỈ áp cho bốn trường; `style` được phép `derived`, `handover`
# được phép `policy` — nới có chủ đích, không phải bỏ sót.
def strict_sql() -> str:
    return " AND ".join([
        nz("mix"), "id_kind = 'official_registry'",
        "building_level IN ('building', 'derived_single')",
        nz("area_m2"), nz("price"), nz("price_kind"),
        nz("style"), nz("handover"), nz("sources"),
        nz("scraped_at"), nz("building_name"), amen_ok(),
        basis_ok("mix_basis"), basis_ok("area_basis"),
        basis_ok("price_basis"), basis_ok("amenities_basis"),
    ])
