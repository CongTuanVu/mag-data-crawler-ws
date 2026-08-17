"""Schema 7 bảng B1–B7 dịch từ features/ws1_building/feature_spec.md.

Mỗi bảng khai báo 3 nhóm cột:
  registry — điền từ bước resolve (định danh toà nhà), không hỏi LLM
  llm      — LLM trích từ raw, mỗi trường kèm provenance
  derived  — code tính lại mỗi lần chạy từ bảng con (feature_spec §13 quy tắc 5)

`json_schema(table)` sinh JSON Schema cho structured output; mọi trường đều
`required` nhưng nullable — buộc model ghi null thay vì bỏ trống (chống bịa số).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Danh mục giá trị — feature_spec §9
# ─────────────────────────────────────────────────────────────────────────────
V = {
    "building_type": ["chung_cu", "mixed_use", "condotel", "officetel", "serviced_apartment"],
    "segment": ["binh_dan", "trung_cap", "cao_cap", "hang_sang", "sieu_sang"],
    "status": ["quy_hoach", "dang_xay", "da_ban_giao", "dang_van_hanh"],
    "handover_standard": ["shell_core", "hoan_thien_co_ban", "noi_that_lien_tuong", "full_furnished"],
    "layout_class": ["studio", "1pn", "1pn_plus", "2pn", "2pn_plus", "3pn", "4pn_plus",
                     "duplex", "penthouse", "sky_villa", "shophouse", "officetel"],
    "massing_form": ["thap_don", "thap_doi", "tower_on_podium", "chu_u", "chu_l", "hop_khoi", "bac_thang"],
    "facade_system": ["curtain_wall", "nhom_kinh_he", "tuong_xay_op", "hon_hop"],
    "balcony_type": ["logia", "ban_cong_nho", "lech_tang", "khong_co"],
    "green_cert": ["LEED", "EDGE", "Green Mark", "LOTUS", "BREEAM", "WELL", "CASBEE", "G-SEED"],
    "area_basis": ["tim_tuong", "thong_thuy", "khong_ro"],
    "room_type": ["phong_khach", "phong_an", "bep", "phong_ngu_master", "phong_ngu_2", "phong_ngu_3",
                  "phong_ngu_4", "wc_master", "wc_chung", "wc_khach", "phong_da_nang", "phong_lam_viec",
                  "phong_giat", "ban_cong", "logia", "sanh_can_ho", "hanh_lang_trong_can", "kho"],
    "room_source": ["floorplan_image", "brochure_text", "listing_table", "manual"],
    "floor_label": ["dien_hinh", "podium", "tang_dich_vu", "penthouse", "tang_ham"],
    "corridor_type": ["hanh_lang_giua", "hanh_lang_ben", "core_trung_tam", "2_can_1_thang"],
    "item_category": ["san", "tuong_tran", "cua", "bep", "thiet_bi_ve_sinh", "dieu_hoa_thong_gio",
                      "thiet_bi_dien", "smart_home", "ban_cong", "thang_may", "an_ninh_pccc"],
    "amenity_category": ["be_boi", "the_thao_gym", "tre_em", "suc_khoe_spa", "cong_dong_su_kien",
                         "thuong_mai_fnb", "canh_quan_vuon", "dich_vu_le_tan", "do_xe", "thu_cung",
                         "khong_gian_lam_viec", "van_hoa_nghe_thuat"],
    "amenity_location": ["tang_ham", "khoi_de", "podium", "tang_trung", "rooftop", "ngoai_troi"],
    "market": ["so_cap", "thu_cap", "cho_thue"],
    "currency": ["VND", "USD", "KRW", "CNY", "SGD", "EUR", "JPY", "TWD", "HKD", "THB"],
    "price_unit": ["per_m2", "per_unit", "per_m2_month"],
    "price_source": ["cdt_official", "san_moi_gioi", "portal_niem_yet", "giao_dich_thuc",
                     "bao_cao_cbre_jll", "bao_chi"],
    "confidence": ["high", "medium", "low"],
}


@dataclass
class F:
    """Một trường. typ ∈ str|float|int|bool|list|pairs|enum:<tên danh mục>"""
    name: str
    typ: str
    desc: str


def _leaf(typ: str) -> Dict[str, Any]:
    if typ.startswith("enum:"):
        return {"type": "string", "enum": V[typ.split(":", 1)[1]]}
    if typ == "list":
        return {"type": "array", "items": {"type": "string"}}
    if typ == "pairs":
        return {"type": "array", "items": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {"type": "number"}},
            "required": ["key", "value"], "additionalProperties": False}}
    return {"str": {"type": "string"}, "float": {"type": "number"},
            "int": {"type": "integer"}, "bool": {"type": "boolean"}}[typ]


def _prop(f: F) -> Dict[str, Any]:
    return {"anyOf": [_leaf(f.typ), {"type": "null"}], "description": f.desc}


@dataclass
class Table:
    name: str            # tên file csv
    label: str           # B1..B7
    unit: str            # đơn vị quan sát, đưa vào prompt
    registry: List[str] = field(default_factory=list)
    llm: List[F] = field(default_factory=list)
    derived: List[str] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)   # thứ tự cột CSV cuối cùng

    @property
    def llm_names(self) -> List[str]:
        return [f.name for f in self.llm]


PROV_ITEM = {
    "type": "object",
    "properties": {
        "field": {"type": "string", "description": "Tên trường trong record"},
        "source_file": {"type": "string", "description": "Tên file raw đã đọc, vd 03_brochure.txt"},
        "snippet": {"type": "string", "description": "Câu/ô nguyên văn chứa giá trị, tối đa 300 ký tự"},
        "confidence": {"type": "string", "enum": V["confidence"]},
    },
    "required": ["field", "source_file", "snippet", "confidence"],
    "additionalProperties": False,
}


def json_schema(t: Table) -> Dict[str, Any]:
    props = {f.name: _prop(f) for f in t.llm}
    props["provenance"] = {
        "type": "array", "items": PROV_ITEM,
        "description": "Mỗi trường có giá trị khác null PHẢI có đúng 1 dòng provenance.",
    }
    return {
        "type": "object",
        "properties": {
            "records": {
                "type": "array",
                "items": {"type": "object", "properties": props,
                          "required": list(props), "additionalProperties": False},
            },
            "notes": {"type": "string", "description": "Ghi chú mâu thuẫn nguồn / lý do bỏ trống"},
        },
        "required": ["records", "notes"],
        "additionalProperties": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# B1 — building
# ─────────────────────────────────────────────────────────────────────────────
B1 = Table(
    name="building", label="B1", unit="1 dòng = 1 toà nhà (chỉ trả về ĐÚNG 1 record)",
    registry=["building_id", "building_name", "linked_case_id", "is_target",
              "country", "city", "official_website"],
    llm=[
        F("building_name_local", "str", "Tên bản địa (Hàn/Trung/Nhật/Việt)"),
        F("project_name", "str", "Dự án mẹ nếu toà là 1 phân khu"),
        F("tower_codes", "list", "Mã các tháp, vd S1, S2, R1"),
        F("district", "str", "Quận/khu"),
        F("address", "str", "Địa chỉ đầy đủ"),
        F("latitude", "float", "Vĩ độ, ±90"),
        F("longitude", "float", "Kinh độ, ±180"),
        F("developer", "str", "Chủ đầu tư"),
        F("brochure_url", "str", "URL brochure/e-catalogue"),
        F("building_type", "enum:building_type", "Loại công trình §8.1"),
        F("segment", "enum:segment", "Phân khúc §8.2"),
        F("status", "enum:status", "Trạng thái §8.3"),
        F("year_launch", "int", "Năm mở bán"),
        F("year_handover", "int", "Năm bàn giao"),
        F("num_towers", "int", "Số tháp"),
        F("num_floors_above", "int", "Số tầng nổi của tháp cao nhất"),
        F("num_basements", "int", "Số tầng hầm"),
        F("height_m", "float", "Chiều cao công trình (m)"),
        F("num_units_total", "int", "Tổng số căn hộ"),
        F("land_area_m2", "float", "Diện tích khu đất (m²)"),
        F("gfa_m2", "float", "Tổng diện tích sàn xây dựng (m²)"),
        F("nfa_sale_m2", "float", "Diện tích sàn thương phẩm (m²)"),
        F("building_density_pct", "float", "Mật độ xây dựng (%)"),
        F("green_area_pct", "float", "Tỷ lệ cây xanh (%)"),
        F("parking_ratio", "float", "Hệ số đỗ xe (chỗ/căn)"),
        F("handover_standard", "enum:handover_standard", "Mức bàn giao chủ đạo §8.4"),
        # §2.2 kiến trúc đặc thù
        F("architect_firm", "str", "Đơn vị thiết kế kiến trúc"),
        F("architect_country", "str", "Quốc tịch đơn vị thiết kế"),
        F("interior_designer", "str", "Đơn vị thiết kế nội thất"),
        F("landscape_architect", "str", "Đơn vị thiết kế cảnh quan"),
        F("architectural_style", "list", "Phong cách kiến trúc — CHỈ gán khi nguồn dùng đúng từ đó"),
        F("design_concept", "str", "Ý tưởng thiết kế, GIỮ NGUYÊN VĂN, không diễn giải"),
        F("design_concept_keywords", "list", "Keyword rút từ design_concept"),
        F("massing_form", "enum:massing_form", "Hình khối tổng thể §8.6"),
        F("facade_material", "list", "Vật liệu mặt đứng"),
        F("facade_system", "enum:facade_system", "Hệ mặt dựng §8.7"),
        F("window_wall_ratio_pct", "float", "Tỷ lệ kính/tường (%)"),
        F("balcony_type", "enum:balcony_type", "Kiểu ban công §8.8"),
        F("floor_to_ceiling_m", "float", "Chiều cao thông thuỷ căn hộ (m)"),
        F("signature_features", "list", "Chi tiết kiến trúc đặc thù, vd sky bridge tầng 30"),
        F("green_cert", "enum:green_cert", "Chứng chỉ xanh §8.9"),
        F("green_cert_level", "str", "Hạng chứng chỉ, vd Gold"),
        F("awards", "list", "Giải thưởng kiến trúc"),
        F("orientation_note", "str", "Ghi chú hướng & tầm nhìn chủ đạo"),
    ],
    derived=["efficiency_ratio_pct", "unit_types_summary", "unit_area_min_m2", "unit_area_max_m2",
             "units_per_floor_typical", "dominant_layout_class", "mix_by_bedroom_pct",
             "handover_brands", "amenity_count", "amenity_highlights"],
)
B1.columns = (["building_id", "building_name", "building_name_local", "project_name", "tower_codes",
               "linked_case_id", "is_target", "country", "city", "district", "address",
               "latitude", "longitude", "developer", "official_website", "brochure_url",
               "building_type", "segment", "status", "year_launch", "year_handover", "num_towers",
               "num_floors_above", "num_basements", "height_m", "num_units_total", "land_area_m2",
               "gfa_m2", "nfa_sale_m2", "efficiency_ratio_pct", "building_density_pct",
               "green_area_pct", "parking_ratio",
               "unit_types_summary", "unit_area_min_m2", "unit_area_max_m2",
               "units_per_floor_typical", "dominant_layout_class", "mix_by_bedroom_pct",
               "handover_standard", "handover_brands", "amenity_count", "amenity_highlights"]
              + [f.name for f in B1.llm if f.name in {
                  "architect_firm", "architect_country", "interior_designer", "landscape_architect",
                  "architectural_style", "design_concept", "design_concept_keywords", "massing_form",
                  "facade_material", "facade_system", "window_wall_ratio_pct", "balcony_type",
                  "floor_to_ceiling_m", "signature_features", "green_cert", "green_cert_level",
                  "awards", "orientation_note"}])

# ─────────────────────────────────────────────────────────────────────────────
# B2 — unit_type
# ─────────────────────────────────────────────────────────────────────────────
B2 = Table(
    name="unit_type", label="B2", unit="1 dòng = 1 loại căn hộ (5–15 dòng/toà)",
    registry=["unit_type_id", "building_id"],
    llm=[
        F("type_code", "str", "BẮT BUỘC. Mã loại căn theo CĐT, vd 2PN-A, Type B1"),
        F("type_name", "str", "Tên thương mại, vd Sky Villa"),
        F("layout_class", "enum:layout_class", "BẮT BUỘC. Chuẩn hoá §8.5"),
        F("bedrooms", "int", "BẮT BUỘC. Số phòng ngủ"),
        F("bathrooms", "float", "Số WC (1.5 = 1 WC đầy đủ + 1 WC khách)"),
        F("has_multipurpose_room", "bool", "Có phòng đa năng (+1)"),
        F("area_gross_m2", "float", "Diện tích TIM TƯỜNG (built-up), m²"),
        F("area_net_m2", "float", "Diện tích THÔNG THUỶ (carpet), m²"),
        F("area_basis_reported", "enum:area_basis", "BẮT BUỘC. Nguồn công bố theo cơ sở nào §8.10"),
        F("area_balcony_m2", "float", "Diện tích ban công/logia, m²"),
        F("num_units_of_type", "int", "Số căn thuộc loại này"),
        F("share_of_total_pct", "float", "Tỷ trọng trong rổ hàng (%)"),
        F("facing", "list", "Hướng căn"),
        F("view_type", "list", "Tầm nhìn"),
        F("is_corner", "bool", "Căn góc"),
        F("floorplan_url", "str", "Link ảnh/PDF mặt bằng căn"),
    ],
    derived=["ratio_net_gross_pct", "floorplan_file"],
)
B2.columns = ["unit_type_id", "building_id", "type_code", "type_name", "layout_class", "bedrooms",
              "bathrooms", "has_multipurpose_room", "area_gross_m2", "area_net_m2",
              "area_basis_reported", "ratio_net_gross_pct", "area_balcony_m2", "num_units_of_type",
              "share_of_total_pct", "facing", "view_type", "is_corner", "floorplan_url",
              "floorplan_file"]

# ─────────────────────────────────────────────────────────────────────────────
# B3 — unit_room
# ─────────────────────────────────────────────────────────────────────────────
B3 = Table(
    name="unit_room", label="B3", unit="1 dòng = 1 phòng trong 1 loại căn",
    registry=["room_id", "unit_type_id"],
    llm=[
        F("type_code", "str", "BẮT BUỘC. Mã loại căn (khớp B2.type_code) mà phòng này thuộc về"),
        F("room_code", "str", "BẮT BUỘC. Mã phòng, vd pn1, wc2, bep"),
        F("room_type", "enum:room_type", "BẮT BUỘC. Loại phòng chuẩn hoá §8.11"),
        F("room_label_raw", "str", "Nhãn gốc trên bản vẽ, vd Master Bedroom, 안방"),
        F("area_m2", "float", "Diện tích phòng (m²)"),
        F("width_m", "float", "Kích thước thông thuỷ cạnh ngắn (m)"),
        F("length_m", "float", "Kích thước thông thuỷ cạnh dài (m)"),
        F("has_window", "bool", "Có cửa sổ / thông thoáng tự nhiên"),
        F("is_ensuite", "bool", "WC khép kín trong phòng ngủ"),
        F("position_note", "str", "Vị trí tương đối, vd giáp ban công"),
        F("source_type", "enum:room_source", "BẮT BUỘC §8.12"),
    ],
)
B3.columns = ["room_id", "unit_type_id", "room_code", "room_type", "room_label_raw", "area_m2",
              "width_m", "length_m", "has_window", "is_ensuite", "position_note", "source_type"]

# ─────────────────────────────────────────────────────────────────────────────
# B4 — floor_plate
# ─────────────────────────────────────────────────────────────────────────────
B4 = Table(
    name="floor_plate", label="B4", unit="1 dòng = 1 dải tầng điển hình của 1 tháp",
    registry=["floor_plate_id", "building_id"],
    llm=[
        F("tower_code", "str", "Mã tháp, vd S1"),
        F("floor_range", "str", "BẮT BUỘC. Dải tầng áp dụng, vd 5-20"),
        F("floor_label", "enum:floor_label", "§8.13"),
        F("units_per_floor", "int", "BẮT BUỘC. Số căn trên 1 sàn"),
        F("unit_type_mix", "pairs", 'Cơ cấu 1 sàn: key = layout_class, value = số căn'),
        F("gfa_per_floor_m2", "float", "Diện tích sàn xây dựng 1 tầng (m²)"),
        F("nfa_per_floor_m2", "float", "Tổng diện tích căn bán được 1 tầng (m²)"),
        F("corridor_type", "enum:corridor_type", "§8.14"),
        F("num_elevators", "int", "Số thang máy khách"),
        F("num_elevators_service", "int", "Số thang hàng/phục vụ"),
        F("num_stairs", "int", "Số thang bộ thoát hiểm"),
        F("core_position", "str", "Vị trí lõi, vd giữa"),
        F("floorplate_url", "str", "Link ảnh mặt bằng tầng"),
    ],
    derived=["efficiency_per_floor_pct", "units_per_elevator"],
)
B4.columns = ["floor_plate_id", "building_id", "tower_code", "floor_range", "floor_label",
              "units_per_floor", "unit_type_mix", "gfa_per_floor_m2", "nfa_per_floor_m2",
              "efficiency_per_floor_pct", "corridor_type", "num_elevators",
              "num_elevators_service", "units_per_elevator", "num_stairs", "core_position",
              "floorplate_url"]

# ─────────────────────────────────────────────────────────────────────────────
# B5 — handover_item
# ─────────────────────────────────────────────────────────────────────────────
B5 = Table(
    name="handover_item", label="B5", unit="1 dòng = 1 hạng mục bàn giao (15–40 dòng/toà)",
    registry=["handover_id", "building_id"],
    llm=[
        F("applies_to_type_code", "str", "Null = áp dụng toàn toà; có giá trị = riêng loại căn đó"),
        F("item_code", "str", "BẮT BUỘC. Slug hạng mục, vd san_phong_khach"),
        F("item_category", "enum:item_category", "BẮT BUỘC §8.15"),
        F("item_name", "str", "BẮT BUỘC. Tên hạng mục"),
        F("item_spec", "str", "Quy cách NGUYÊN VĂN"),
        F("brand", "str", "Thương hiệu"),
        F("brand_origin", "str", "Xuất xứ"),
        F("is_included", "bool", "BẮT BUỘC. true chỉ khi nằm dưới mục bàn giao chuẩn; "
                                 "có từ khoá option/nâng cấp/upgrade/phụ thu → false"),
        F("note", "str", "Ghi chú"),
    ],
    derived=["applies_to_unit_type_id"],
)
B5.columns = ["handover_id", "building_id", "applies_to_unit_type_id", "item_code", "item_category",
              "item_name", "item_spec", "brand", "brand_origin", "is_included", "note"]

# ─────────────────────────────────────────────────────────────────────────────
# B6 — amenity
# ─────────────────────────────────────────────────────────────────────────────
B6 = Table(
    name="amenity", label="B6", unit="1 dòng = 1 tiện ích NỘI KHU CỦA TOÀ (10–50 dòng/toà)",
    registry=["amenity_id", "building_id"],
    llm=[
        F("slug", "str", "BẮT BUỘC. Slug tiện ích, snake_case ascii"),
        F("amenity_category", "enum:amenity_category", "BẮT BUỘC §8.16"),
        F("amenity_name", "str", "BẮT BUỘC. Tên tiện ích (tiếng Việt)"),
        F("amenity_name_local", "str", "Tên bản địa"),
        F("location", "enum:amenity_location", "§8.17"),
        F("floor_level", "str", "Tầng cụ thể, vd B1, 5, mái"),
        F("area_m2", "float", "Diện tích tiện ích (m²)"),
        F("is_indoor", "bool", "Trong nhà"),
        F("is_resident_free", "bool", "Miễn phí cho cư dân"),
        F("operator_brand", "str", "Đơn vị vận hành"),
        F("is_highlight", "bool", "Tiện ích điểm nhấn"),
    ],
)
B6.columns = ["amenity_id", "building_id", "amenity_category", "amenity_name", "amenity_name_local",
              "location", "floor_level", "area_m2", "is_indoor", "is_resident_free",
              "operator_brand", "is_highlight"]

# ─────────────────────────────────────────────────────────────────────────────
# B7 — price_obs
# ─────────────────────────────────────────────────────────────────────────────
B7 = Table(
    name="price_obs", label="B7", unit="1 dòng = 1 quan sát giá (loại căn × thị trường × kỳ)",
    registry=["price_id", "building_id", "observed_at"],
    llm=[
        F("unit_type_code", "str", "Mã loại căn (khớp B2.type_code). Null = giá bình quân toàn toà"),
        F("market", "enum:market", "BẮT BUỘC. so_cap = CĐT mở bán, thu_cap = chuyển nhượng"),
        F("price_min", "float", "Giá thấp nhất, đã quy đổi theo §10"),
        F("price_max", "float", "Giá cao nhất"),
        F("price_avg", "float", "Giá bình quân"),
        F("currency", "enum:currency", "BẮT BUỘC"),
        F("price_unit", "enum:price_unit", "BẮT BUỘC"),
        F("price_basis", "enum:area_basis", "BẮT BUỘC. Giá/m² tính trên cơ sở nào §8.10"),
        F("includes_vat", "bool", "Đã gồm VAT chưa. Không nêu → null, KHÔNG mặc định false"),
        F("includes_maintenance_fee", "bool", "Đã gồm phí bảo trì 2% chưa. Không nêu → null"),
        F("period", "str", "BẮT BUỘC. Kỳ quan sát YYYY-MM hoặc YYYY-Qn"),
        F("sample_size", "int", "Số tin rao / số giao dịch dùng để tính"),
        F("source_type", "enum:price_source", "BẮT BUỘC §8.18"),
        F("listing_url", "str", "URL tin rao / báo cáo"),
    ],
    derived=["unit_type_id", "fx_rate_to_usd", "price_usd_per_m2"],
)
B7.columns = ["price_id", "building_id", "unit_type_id", "market", "price_min", "price_max",
              "price_avg", "currency", "price_unit", "price_basis", "includes_vat",
              "includes_maintenance_fee", "period", "observed_at", "sample_size", "source_type",
              "fx_rate_to_usd", "price_usd_per_m2", "listing_url"]

TABLES: Dict[str, Table] = {t.name: t for t in (B1, B2, B3, B4, B5, B6, B7)}
TEXT_TABLES = ["building", "unit_type", "floor_plate", "handover_item", "amenity", "price_obs"]

PROVENANCE_COLUMNS = ["table", "record_key", "field", "value", "source_url", "source_file",
                      "snippet", "confidence", "accessed_at"]

BENCHMARK_EXTRA = ["num_unit_types", "num_rooms_captured", "num_handover_items", "num_amenities",
                   "num_price_obs", "price_usd_per_m2_primary", "price_usd_per_m2_secondary",
                   "secondary_premium_pct", "price_growth_pct_yoy", "sources_ok", "extracted_at"]
