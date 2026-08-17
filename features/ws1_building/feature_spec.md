# Feature Spec — WS2 Building (Toà nhà / Dự án căn hộ)

Domain: **toà nhà ở & mixed-use trên thế giới**. Mục tiêu: chuẩn hoá bộ tiêu chí
benchmark **sản phẩm toà nhà** (layout, kiến trúc, bàn giao, tiện ích, giá, product
mix) để đối chiếu với sản phẩm dự kiến của **Gia Bình Airport City (GBAC)**.

Quan hệ với WS1: WS1 benchmark ở cấp **khu đô thị**; WS2 khoan xuống cấp **toà nhà
bên trong khu đô thị đó**. Mỗi toà giữ khoá ngoại `linked_case_id` trỏ về
`case_benchmark.case_id` của WS1 (vd `incheon_songdo`, `vinhomes_ocean_park`).

- **Phạm vi:** chung cư / tháp căn hộ / mixed-use **để bán**, ưu tiên các toà nằm
  trong 8 KĐT đã benchmark ở WS1. Không gồm toà văn phòng thuần cho thuê.
- **Nguồn feature:** yêu cầu nghiệp vụ 6 nhóm (mục 0.1), diễn giải theo thực tiễn
  thị trường BĐS Việt Nam + chuẩn quốc tế.

---

## 0. Mô hình dữ liệu

### 0.1 Ánh xạ 6 nhóm feature yêu cầu → bảng

| # | Yêu cầu nghiệp vụ | Đơn vị quan sát đúng | Bảng |
|---|---|---|---|
| 1 | Diện tích layout — phòng nào, bao nhiêu m² | 1 **phòng** trong 1 loại căn | **B3** `unit_room` |
| 2 | Thiết kế kiến trúc đặc thù | 1 **toà nhà** | **B1** `building` (khối §2.2) |
| 3 | Tiêu chuẩn bàn giao | 1 **hạng mục bàn giao** của toà | **B5** `handover_item` |
| 4 | Tiện ích nội khu | 1 **tiện ích** của toà | **B6** `amenity` |
| 5 | Giá sơ cấp / giá thứ cấp | 1 **quan sát giá** (loại căn × thị trường × kỳ) | **B7** `price_obs` |
| 6 | Product Mix — cơ cấu 1 sàn, loại hình căn hộ | 1 **mặt bằng tầng** + 1 **loại căn** | **B4** `floor_plate` + **B2** `unit_type` |

### 0.2 Sơ đồ

```
                        ┌─────────────────────────────┐
                        │  B1. building               │  1 dòng = 1 toà nhà
                        │  key: building_id           │  + kiến trúc đặc thù (§2.2)
                        │  FK → ws1.case_id           │
                        └──┬────┬────┬────┬───────────┘
                     1:N   │    │    │    │   1:N
        ┌──────────────────┘    │    │    └──────────────────┐
        │                  1:N  │    │  1:N                  │
        ▼                       ▼    ▼                       ▼
┌──────────────────┐  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ B2. unit_type    │  │ B4.floor_    │ │ B5.handover_ │ │ B6. amenity  │
│ key: unit_type_id│  │    plate     │ │    item      │ │ key:amenity_id│
│ 1 = 1 loại căn   │  │ 1 = 1 mặt    │ │ 1 = 1 hạng   │ │ 1 = 1 tiện   │
└────┬─────────┬───┘  │   bằng tầng  │ │   mục BG     │ │   ích nội khu│
 1:N │         │ 1:N  └──────────────┘ └──────────────┘ └──────────────┘
     ▼         ▼
┌──────────────┐ ┌────────────────────────┐
│ B3.unit_room │ │ B7. price_obs          │  1 dòng = (toà × loại căn ×
│ 1 = 1 phòng  │ │ key: price_id          │   sơ cấp/thứ cấp × kỳ)
└──────────────┘ └────────────────────────┘
```

| Bảng | Đơn vị quan sát | Khoá | Ước lượng quy mô / toà |
|---|---|---|---|
| **B1** `building` | 1 toà nhà | `building_id` | 1 |
| **B2** `unit_type` | 1 loại căn hộ | `unit_type_id` | 5–15 |
| **B3** `unit_room` | 1 phòng trong 1 loại căn | `room_id` | 6–12 / loại căn |
| **B4** `floor_plate` | 1 dải tầng điển hình / tháp | `floor_plate_id` | 1–4 |
| **B5** `handover_item` | 1 hạng mục bàn giao | `handover_id` | 15–40 |
| **B6** `amenity` | 1 tiện ích nội khu | `amenity_id` | 10–50 |
| **B7** `price_obs` | 1 quan sát giá | `price_id` | 10–100 (nhiều kỳ) |

**Nguyên tắc phân bổ:** giá trị **mô tả bản thân toà** → B1. Giá trị là **một phần
tử trong danh sách** (phòng, tiện ích, hạng mục bàn giao, loại căn) → bảng con,
**không nhồi thành chuỗi phân tách bằng `;` trong B1**. B1 chỉ giữ bản **tóm tắt
dạng list keyword + số đếm** để `build_html.py` render nhanh, không dùng làm nguồn
sự thật.

---

## 1. B1 — `building`

### 1.1 Định danh & vị trí

| name | type | required | nguồn | transform / đơn vị | mô tả |
|---|---|---|---|---|---|
| `building_id` | string | yes | registry | slug snake_case | Khoá. Vd `songdo_central_park_ipark` |
| `building_name` | string | yes | registry | strip | Tên toà/dự án (tiếng Anh hoặc quốc tế) |
| `building_name_local` | string | no | web | strip | Tên bản địa (Hàn/Trung/Việt) |
| `project_name` | string | no | web | strip | Dự án mẹ nếu toà là 1 phân khu |
| `tower_codes` | list | no | web | tách `;` | Mã các tháp (S1, S2, R1…) |
| `linked_case_id` | string | no | registry | — | FK → WS1 `case_benchmark.case_id` |
| `is_target` | bool | yes | registry | GBAC = true | Có phải sản phẩm GBAC không |
| `country` | string | yes | registry | — | Quốc gia |
| `city` | string | yes | registry | — | Thành phố |
| `district` | string | no | web | strip | Quận/khu |
| `address` | string | no | web | strip | Địa chỉ |
| `latitude` | float | no | web | ±90 | Vĩ độ |
| `longitude` | float | no | web | ±180 | Kinh độ |
| `developer` | string | no | web | strip | Chủ đầu tư |
| `official_website` | string | no | registry | url | Trang chính thức |
| `brochure_url` | string\|null | no | web | url | Brochure/e-catalogue (thường PDF) |

### 1.2 Quy mô & trạng thái

| name | type | required | đơn vị | mô tả |
|---|---|---|---|---|
| `building_type` | enum | yes | §8.1 | `chung_cu` \| `mixed_use` \| `condotel` \| `officetel` \| `serviced_apartment` |
| `segment` | enum | no | §8.2 | `binh_dan` \| `trung_cap` \| `cao_cap` \| `hang_sang` \| `sieu_sang` |
| `status` | enum | no | §8.3 | `quy_hoach` \| `dang_xay` \| `da_ban_giao` \| `dang_van_hanh` |
| `year_launch` | int | no | năm | Năm mở bán |
| `year_handover` | int | no | năm | Năm bàn giao |
| `num_towers` | int | no | tháp | Số tháp |
| `num_floors_above` | int | no | tầng | Số tầng nổi (tháp cao nhất) |
| `num_basements` | int | no | tầng | Số tầng hầm |
| `height_m` | float | no | m | Chiều cao công trình |
| `num_units_total` | int | no | căn | Tổng số căn hộ |
| `land_area_m2` | float | no | m² | Diện tích khu đất |
| `gfa_m2` | float | no | m² | Tổng diện tích sàn xây dựng (GFA) |
| `nfa_sale_m2` | float | no | m² | Diện tích sàn thương phẩm (bán được) |
| `efficiency_ratio_pct` | float | no | %, 0–100 | Hệ số sử dụng = `nfa_sale_m2 / gfa_m2` |
| `building_density_pct` | float | no | %, 0–100 | Mật độ xây dựng |
| `green_area_pct` | float | no | %, 0–100 | Tỷ lệ cây xanh |
| `parking_ratio` | float | no | chỗ/căn | Hệ số đỗ xe |

### 1.3 Tóm tắt product mix (derived — nguồn sự thật ở B2/B4)

| name | type | đơn vị | mô tả |
|---|---|---|---|
| `unit_types_summary` | list | — | Danh sách `layout_class` có bán (`studio; 1PN; 2PN; 3PN`) |
| `unit_area_min_m2` | float | m² | Min `area_net_m2` toàn rổ hàng |
| `unit_area_max_m2` | float | m² | Max `area_net_m2` toàn rổ hàng |
| `units_per_floor_typical` | int | căn | Số căn/sàn của dải tầng điển hình chính |
| `dominant_layout_class` | enum | §8.5 | Loại căn chiếm tỷ trọng lớn nhất |
| `mix_by_bedroom_pct` | obj | % | `{"1":15,"2":55,"3":30}` — cơ cấu theo số PN |

### 1.4 Tóm tắt bàn giao & tiện ích (derived)

| name | type | mô tả |
|---|---|---|
| `handover_standard` | enum §8.4 | Mức bàn giao chủ đạo của toà |
| `handover_brands` | list | Thương hiệu thiết bị nổi bật (Hansgrohe; Bosch; Duravit…) |
| `amenity_count` | int | Số tiện ích trong B6 |
| `amenity_highlights` | list | 5–8 tiện ích điểm nhấn |

---

## 2. B1 §2.2 — Thiết kế kiến trúc đặc thù  ← *feature #2*

| name | type | required | nguồn | mô tả | ví dụ |
|---|---|---|---|---|---|
| `architect_firm` | string | no | trang dự án / báo kiến trúc | Đơn vị thiết kế kiến trúc | `Kohn Pedersen Fox` |
| `architect_country` | string | no | — | Quốc tịch đơn vị TK | `Hoa Kỳ` |
| `interior_designer` | string | no | — | Đơn vị thiết kế nội thất | — |
| `landscape_architect` | string | no | — | Đơn vị thiết kế cảnh quan | — |
| `architectural_style` | list | no | mô tả dự án | Phong cách kiến trúc | `tân cổ điển`; `modern tropical`; `art deco` |
| `design_concept` | string | no | mô tả dự án | Ý tưởng thiết kế, giữ nguyên văn | "lấy cảm hứng từ cánh buồm…" |
| `design_concept_keywords` | list | no | derived | Keyword rút từ `design_concept` | `cánh buồm`; `sóng nước` |
| `massing_form` | enum §8.6 | no | mặt bằng/phối cảnh | Hình khối tổng thể | `tower_on_podium` |
| `facade_material` | list | no | web | Vật liệu mặt đứng | `kính low-E`; `nhôm định hình`; `đá tự nhiên` |
| `facade_system` | enum §8.7 | no | web | Hệ mặt dựng | `curtain_wall` |
| `window_wall_ratio_pct` | float | no | web | Tỷ lệ kính/tường (%) | 60 |
| `balcony_type` | enum §8.8 | no | mặt bằng | Kiểu ban công | `logia` \| `ban_cong_nhô` \| `lech_tang` \| `khong_co` |
| `floor_to_ceiling_m` | float | no | brochure | Chiều cao thông thuỷ căn hộ (m) | 2.8 |
| `signature_features` | list | no | web | Chi tiết kiến trúc đặc thù | `sky bridge tầng 30`; `hồ bơi vô cực rooftop`; `podium vườn bậc thang` |
| `green_cert` | enum §8.9 | no | trang chứng chỉ | Chứng chỉ xanh | `LEED` \| `EDGE` \| `Green Mark` \| `LOTUS` \| `BREEAM` |
| `green_cert_level` | string | no | — | Hạng chứng chỉ | `Gold` |
| `awards` | list | no | web/báo | Giải thưởng kiến trúc | `CTBUH Award 2023` |
| `orientation_note` | string | no | brochure | Ghi chú hướng & tầm nhìn chủ đạo | "hướng Đông Nam nhìn công viên" |

> **Cảnh báo trích xuất:** `design_concept` là văn marketing — extractor giữ
> **nguyên văn câu gốc** vào `snippet`, không diễn giải lại. `architectural_style`
> chỉ gán khi nguồn dùng đúng từ đó, không suy đoán từ ảnh phối cảnh.

---

## 3. B2 — `unit_type` (loại hình căn hộ)  ← *feature #6a*

| name | type | required | đơn vị | mô tả |
|---|---|---|---|---|
| `unit_type_id` | string | yes | — | Khoá = `{building_id}__{type_code}` |
| `building_id` | string | yes | — | FK → B1 |
| `type_code` | string | yes | — | Mã loại căn theo CĐT. Vd `2PN-A`, `Type B1` |
| `type_name` | string | no | — | Tên thương mại. Vd `Sky Villa`, `Duplex Garden` |
| `layout_class` | enum §8.5 | yes | — | Chuẩn hoá: `studio`\|`1pn`\|`1pn_plus`\|`2pn`\|`2pn_plus`\|`3pn`\|`4pn_plus`\|`duplex`\|`penthouse`\|`sky_villa`\|`shophouse`\|`officetel` |
| `bedrooms` | int | yes | phòng | Số phòng ngủ |
| `bathrooms` | float | no | phòng | Số WC (1.5 = 1 WC đầy đủ + 1 WC khách) |
| `has_multipurpose_room` | bool | no | — | Có phòng đa năng (`+1`) không |
| **`area_gross_m2`** | float | no | m² | **Diện tích tim tường** (built-up) |
| **`area_net_m2`** | float | no | m² | **Diện tích thông thuỷ** (carpet) — cơ sở HĐMB tại VN |
| `area_basis_reported` | enum §8.10 | yes | — | Nguồn công bố theo cơ sở nào: `tim_tuong`\|`thong_thuy`\|`khong_ro` |
| `ratio_net_gross_pct` | float | no | %, 0–100 | `area_net_m2 / area_gross_m2` — derived, chỉ tính khi có **cả hai** |
| `area_balcony_m2` | float | no | m² | Diện tích ban công/logia |
| `num_units_of_type` | int | no | căn | Số căn thuộc loại này |
| `share_of_total_pct` | float | no | %, 0–100 | Tỷ trọng trong rổ hàng |
| `facing` | list | no | — | Hướng căn (`Đông Nam`; `Tây Bắc`) |
| `view_type` | list | no | — | Tầm nhìn (`công viên`; `sông`; `nội khu`; `sân bay`) |
| `is_corner` | bool | no | — | Căn góc |
| `floorplan_url` | string | no | — | Link ảnh/PDF mặt bằng căn |
| `floorplan_file` | string | no | — | Đường dẫn file đã crawl trong `raw/` |

> **Bắt buộc ghi `area_basis_reported`.** Brochure Việt Nam thường công bố **tim
> tường**, hợp đồng mua bán dùng **thông thuỷ** — chênh **6–10%**. Extractor
> **không được tự quy đổi** giữa hai cơ sở; thiếu một trong hai → để `null`.

---

## 4. B3 — `unit_room` (diện tích layout từng phòng)  ← *feature #1*

Đơn vị nhỏ nhất của spec: **1 dòng = 1 phòng trong 1 loại căn hộ**.

| name | type | required | đơn vị | mô tả |
|---|---|---|---|---|
| `room_id` | string | yes | — | Khoá = `{unit_type_id}__{room_code}` |
| `unit_type_id` | string | yes | — | FK → B2 |
| `room_code` | string | yes | — | Mã phòng. Vd `pn1`, `wc2`, `bep` |
| `room_type` | enum §8.11 | yes | — | Loại phòng chuẩn hoá |
| `room_label_raw` | string | no | — | Nhãn gốc trên bản vẽ. Vd `Master Bedroom`, `PN1`, `안방` |
| `area_m2` | float | no | m² | Diện tích phòng |
| `width_m` | float | no | m | Kích thước thông thuỷ cạnh ngắn |
| `length_m` | float | no | m | Kích thước thông thuỷ cạnh dài |
| `has_window` | bool | no | — | Có cửa sổ / thông thoáng tự nhiên |
| `is_ensuite` | bool | no | — | WC khép kín trong phòng ngủ |
| `position_note` | string | no | — | Vị trí tương đối. Vd `giáp ban công`, `cạnh bếp` |
| `source_type` | enum §8.12 | yes | — | `floorplan_image`\|`brochure_text`\|`listing_table`\|`manual` |

### 4.1 Ràng buộc kiểm tra

- `sum(area_m2)` của các phòng thuộc 1 `unit_type` **phải ≤ `area_gross_m2`** của
  loại căn đó. Vượt → log cảnh báo, đặt `confidence = low`, **không tự sửa số**.
- Chênh lệch `area_gross_m2 − sum(area_m2)` = tường + hộp kỹ thuật, hợp lý ở mức
  **5–15%**. Ngoài dải này → cảnh báo.
- Số dòng `room_type` bắt đầu bằng `phong_ngu_` **phải khớp** `B2.bedrooms`.

### 4.2 Cảnh báo khả thi (rủi ro lớn nhất của WS2)

Diện tích từng phòng **hầu như chỉ tồn tại dưới dạng ảnh bản vẽ mặt bằng** (PNG/PDF
trong brochure), không có ở dạng text. Do đó:

- Extractor **deterministic không đọc được B3** từ `.txt` đã crawl.
- Luồng thực tế: `crawl_sources.py` tải brochure → lưu ảnh mặt bằng vào
  `raw/<building>/floorplans/` → **nhập tay hoặc OCR có người xác nhận** vào một
  file trung gian `refer_file/<building>_rooms.csv` → extractor đọc file đó.
- Mọi dòng B3 chưa được người xác nhận: `confidence = low`, `source_type =
  floorplan_image`. **Không đoán diện tích phòng từ tổng diện tích căn.**

---

## 5. B4 — `floor_plate` (cơ cấu căn hộ 1 sàn)  ← *feature #6b*

| name | type | required | đơn vị | mô tả |
|---|---|---|---|---|
| `floor_plate_id` | string | yes | — | Khoá = `{building_id}__{tower_code}__{floor_range}` |
| `building_id` | string | yes | — | FK → B1 |
| `tower_code` | string | no | — | Mã tháp. Vd `S1` |
| `floor_range` | string | yes | — | Dải tầng áp dụng. Vd `5-20` |
| `floor_label` | enum §8.13 | no | — | `dien_hinh`\|`podium`\|`tang_dich_vu`\|`penthouse` |
| `units_per_floor` | int | yes | căn | **Số căn trên 1 sàn** |
| `unit_type_mix` | obj | no | căn | Cơ cấu 1 sàn theo loại căn. Vd `{"1pn":2,"2pn":6,"3pn":2}` |
| `gfa_per_floor_m2` | float | no | m² | Diện tích sàn xây dựng 1 tầng |
| `nfa_per_floor_m2` | float | no | m² | Tổng diện tích căn bán được 1 tầng |
| `efficiency_per_floor_pct` | float | no | %, 0–100 | `nfa/gfa` — hệ số sử dụng sàn |
| `corridor_type` | enum §8.14 | no | — | `hanh_lang_giua`\|`hanh_lang_ben`\|`core_trung_tam`\|`2_can_1_thang` |
| `num_elevators` | int | no | thang | Số thang máy khách |
| `num_elevators_service` | int | no | thang | Số thang hàng/thang phục vụ |
| `units_per_elevator` | float | no | căn/thang | Derived — chỉ số tiện nghi vận hành |
| `num_stairs` | int | no | thang | Số thang bộ thoát hiểm |
| `core_position` | string | no | — | Vị trí lõi. Vd `giữa`, `lệch một đầu` |
| `floorplate_url` | string | no | — | Link ảnh mặt bằng tầng |

> **Không ép 1 con số duy nhất vào B1.** Một toà có thể có nhiều tháp và nhiều dải
> tầng với số căn/sàn khác nhau → tạo **nhiều dòng B4**. `B1.units_per_floor_typical`
> chỉ lấy từ dòng `floor_label = dien_hinh` có `units_per_floor` lớn nhất.

---

## 6. B5 — `handover_item` (tiêu chuẩn bàn giao)  ← *feature #3*

| name | type | required | mô tả |
|---|---|---|---|
| `handover_id` | string | yes | Khoá = `{building_id}__{item_code}` |
| `building_id` | string | yes | FK → B1 |
| `applies_to_unit_type_id` | string\|null | no | Null = áp dụng toàn toà; có giá trị = riêng loại căn đó |
| `item_code` | string | yes | Slug hạng mục. Vd `san_phong_khach` |
| `item_category` | enum §8.15 | yes | Nhóm hạng mục |
| `item_name` | string | yes | Tên hạng mục. Vd `Sàn phòng khách` |
| `item_spec` | string | no | Quy cách nguyên văn. Vd `Gỗ công nghiệp AC4 dày 12mm` |
| `brand` | string | no | Thương hiệu. Vd `Hansgrohe` |
| `brand_origin` | string | no | Xuất xứ. Vd `Đức` |
| `is_included` | bool | yes | Có bàn giao kèm hay là tuỳ chọn thêm tiền |
| `note` | string | no | Ghi chú (option, phụ thu, theo gói…) |

Ngoài ra ở **B1**: `handover_standard` (enum §8.4) — mức bàn giao chủ đạo:
`shell_core` (thô) · `hoan_thien_co_ban` (cơ bản) · `noi_that_lien_tuong` (liền
tường) · `full_furnished` (đầy đủ nội thất rời).

> **Nhiễu thường gặp:** brochure liệt kê thiết bị ở mục "tuỳ chọn nâng cấp" lẫn với
> mục bàn giao chuẩn. Extractor chỉ đặt `is_included = true` khi câu gốc nằm dưới
> tiêu đề bàn giao/handover/specification; mục có từ khoá `option`, `nâng cấp`,
> `upgrade`, `phụ thu` → `is_included = false`.

---

## 7. B6 — `amenity` (tiện ích nội khu)  ← *feature #4*

| name | type | required | đơn vị | mô tả |
|---|---|---|---|---|
| `amenity_id` | string | yes | — | Khoá = `{building_id}__{slug}` |
| `building_id` | string | yes | — | FK → B1 |
| `amenity_category` | enum §8.16 | yes | — | Nhóm tiện ích |
| `amenity_name` | string | yes | — | Tên tiện ích |
| `amenity_name_local` | string | no | — | Tên bản địa |
| `location` | enum §8.17 | no | — | `tang_ham`\|`khoi_de`\|`podium`\|`tang_trung`\|`rooftop`\|`ngoai_troi` |
| `floor_level` | string | no | tầng | Tầng cụ thể. Vd `B1`, `5`, `mái` |
| `area_m2` | float | no | m² | Diện tích tiện ích |
| `is_indoor` | bool | no | — | Trong nhà / ngoài trời |
| `is_resident_free` | bool | no | — | Miễn phí cho cư dân hay thu thêm |
| `operator_brand` | string | no | — | Đơn vị vận hành. Vd `California Fitness` |
| `is_highlight` | bool | no | — | Tiện ích điểm nhấn (dùng cho `B1.amenity_highlights`) |

> Phân biệt **tiện ích nội khu của toà** (B6) với **tiện ích cấp khu đô thị**
> (đã nằm ở WS1 `basic_amenities` / `highlight_amenities`). Trường học, bệnh viện,
> TTTM ngoài ranh giới toà → thuộc WS1, **không nhập vào B6**.

---

## 8. B7 — `price_obs` (giá sơ cấp & thứ cấp)  ← *feature #5*

**1 dòng = 1 quan sát giá**, vì giá thay đổi theo loại căn, theo thị trường
(sơ cấp/thứ cấp) và theo thời điểm. Ép 1 con số vào B1 sẽ mất khả năng so sánh.

| name | type | required | đơn vị | mô tả |
|---|---|---|---|---|
| `price_id` | string | yes | — | Khoá = `{building_id}__{unit_type_id\|all}__{market}__{period}` |
| `building_id` | string | yes | — | FK → B1 |
| `unit_type_id` | string\|null | no | — | Null = giá bình quân toàn toà |
| **`market`** | enum | yes | — | **`so_cap`** (CĐT mở bán) \| **`thu_cap`** (chuyển nhượng) \| `cho_thue` |
| `price_min` | float | no | — | Giá thấp nhất |
| `price_max` | float | no | — | Giá cao nhất |
| `price_avg` | float | no | — | Giá bình quân |
| `currency` | enum | yes | — | `VND`\|`USD`\|`KRW`\|`CNY`\|`SGD`\|`EUR` |
| `price_unit` | enum | yes | — | `per_m2` \| `per_unit` \| `per_m2_month` (thuê) |
| **`price_basis`** | enum §8.10 | yes | — | Giá/m² tính trên **`tim_tuong`** hay **`thong_thuy`** hay `khong_ro` |
| `includes_vat` | bool\|null | yes | — | Đã gồm VAT chưa (VN: 8–10%) |
| `includes_maintenance_fee` | bool\|null | yes | — | Đã gồm phí bảo trì 2% chưa (VN) |
| `period` | string | yes | — | Kỳ quan sát `YYYY-MM` hoặc `YYYY-Qn` |
| `observed_at` | date | yes | — | Ngày crawl được giá |
| `sample_size` | int | no | tin/GD | Số tin rao hoặc số giao dịch dùng để tính |
| `source_type` | enum §8.18 | yes | — | `cdt_official`\|`san_moi_gioi`\|`portal_niem_yet`\|`giao_dich_thuc`\|`bao_cao_cbre_jll`\|`bao_chi` |
| `fx_rate_to_usd` | float | no | — | Tỷ giá dùng khi chuẩn hoá |
| `price_usd_per_m2` | float | no | USD/m² | **Derived** — cột duy nhất dùng để so sánh chéo quốc gia |
| `listing_url` | string | no | — | URL tin rao / báo cáo |

### 8.1 Chỉ số phái sinh (tính sau khi có ≥2 kỳ)

| name | type | công thức |
|---|---|---|
| `price_growth_pct_yoy` | float | `(price_avg[t] / price_avg[t−1y] − 1) × 100` |
| `secondary_premium_pct` | float | `(giá thứ cấp / giá sơ cấp cùng loại căn − 1) × 100` |

> **Không tính `secondary_premium_pct` khi hai vế khác `price_basis` hoặc khác
> `includes_vat`** — sai số cộng dồn có thể vượt 15%, lớn hơn chính chênh lệch cần đo.

---

## 9. Danh mục giá trị (controlled vocabulary)

**§8.1 `building_type`** · `chung_cu` · `mixed_use` · `condotel` · `officetel` · `serviced_apartment`

**§8.2 `segment`** · `binh_dan` · `trung_cap` · `cao_cap` · `hang_sang` · `sieu_sang`

**§8.3 `status`** · `quy_hoach` · `dang_xay` · `da_ban_giao` · `dang_van_hanh`

**§8.4 `handover_standard`** · `shell_core` · `hoan_thien_co_ban` · `noi_that_lien_tuong` · `full_furnished`

**§8.5 `layout_class`** · `studio` · `1pn` · `1pn_plus` · `2pn` · `2pn_plus` · `3pn` · `4pn_plus` · `duplex` · `penthouse` · `sky_villa` · `shophouse` · `officetel`

**§8.6 `massing_form`** · `thap_don` · `thap_doi` · `tower_on_podium` · `chu_u` · `chu_l` · `hop_khoi` · `bac_thang`

**§8.7 `facade_system`** · `curtain_wall` · `nhom_kinh_he` · `tuong_xay_op` · `hon_hop`

**§8.8 `balcony_type`** · `logia` · `ban_cong_nho` · `lech_tang` · `khong_co`

**§8.9 `green_cert`** · `LEED` · `EDGE` · `Green Mark` · `LOTUS` · `BREEAM` · `WELL` · `CASBEE` · `G-SEED`

**§8.10 `area_basis` / `price_basis`** · `tim_tuong` · `thong_thuy` · `khong_ro`

**§8.11 `room_type`** · `phong_khach` · `phong_an` · `bep` · `phong_ngu_master` · `phong_ngu_2` · `phong_ngu_3` · `phong_ngu_4` · `wc_master` · `wc_chung` · `wc_khach` · `phong_da_nang` · `phong_lam_viec` · `phong_giat` · `ban_cong` · `logia` · `sanh_can_ho` · `hanh_lang_trong_can` · `kho`

**§8.12 `source_type` (B3)** · `floorplan_image` · `brochure_text` · `listing_table` · `manual`

**§8.13 `floor_label`** · `dien_hinh` · `podium` · `tang_dich_vu` · `penthouse` · `tang_ham`

**§8.14 `corridor_type`** · `hanh_lang_giua` · `hanh_lang_ben` · `core_trung_tam` · `2_can_1_thang`

**§8.15 `item_category` (bàn giao)** · `san` · `tuong_tran` · `cua` · `bep` · `thiet_bi_ve_sinh` · `dieu_hoa_thong_gio` · `thiet_bi_dien` · `smart_home` · `ban_cong` · `thang_may` · `an_ninh_pccc`

**§8.16 `amenity_category`** · `be_boi` · `the_thao_gym` · `tre_em` · `suc_khoe_spa` · `cong_dong_su_kien` · `thuong_mai_fnb` · `canh_quan_vuon` · `dich_vu_le_tan` · `do_xe` · `thu_cung` · `khong_gian_lam_viec` · `van_hoa_nghe_thuat`

**§8.17 `location` (tiện ích)** · `tang_ham` · `khoi_de` · `podium` · `tang_trung` · `rooftop` · `ngoai_troi`

**§8.18 `source_type` (giá)** · `cdt_official` · `san_moi_gioi` · `portal_niem_yet` · `giao_dich_thuc` · `bao_cao_cbre_jll` · `bao_chi`

---

## 10. Quy đổi đơn vị tại nguồn

Áp dụng `field_num(..., factor=)` như WS1 — quy đổi **ngay khi trích**, để `record`
luôn đúng đơn vị đã khai báo.

| Thị trường | Nguồn công bố | Trường đích | factor |
|---|---|---|---|
| Hàn Quốc | `평` (pyeong) | `area_gross_m2`, `area_m2` | `3.30579` |
| Đài Loan | `坪` (ping) | `area_gross_m2` | `3.30579` |
| HK / Singapore / Mỹ | `sq ft` | `area_*_m2` | `0.092903` |
| Trung Quốc | `建筑面积` (kiến trúc) → tim tường; `套内面积` → thông thuỷ | `area_basis_reported` | mapping, không nhân |
| Hàn Quốc | `억원` (100 triệu KRW) | `price_*` | `1e8` |
| Trung Quốc | `万元` (10 nghìn CNY) | `price_*` | `1e4` |
| Việt Nam | `tỷ đồng` | `price_*` (VND) | `1e9` |
| Việt Nam | `triệu/m²` | `price_*` (VND) | `1e6` |
| Nhật Bản | `帖` / `畳` (jō) | `area_m2` (B3) | `1.62` |
| Nhật Bản | `坪` (tsubo) | `area_*_m2` | `3.30579` |
| Nhật Bản | `万円` (10 nghìn JPY) | `price_*` (JPY) | `1e4` |
| Nhật Bản | `億円` (100 triệu JPY) | `price_*` (JPY) | `1e8` |
| Nhật Bản | `壁芯面積` → tim tường; `専有面積`/`内法面積` → thông thuỷ | `area_basis_reported` | mapping, không nhân |

> Diện tích Hàn Quốc: **`공급면적`** (supply area) ≈ tim tường + phần chung →
> `tim_tuong`; **`전용면적`** (exclusive area) ≈ thông thuỷ → `thong_thuy`. Ghi
> đúng `area_basis_reported`, chênh giữa hai loại tại Hàn thường **20–30%**, lớn
> hơn nhiều so với chênh tim tường/thông thuỷ tại VN.

> **Nhật Bản — `間取り` sang `layout_class` (§8.5):** `nLDK` → `npn` (LDK là phòng
> khách–ăn–bếp liên thông, **không** tính là phòng ngủ): `1LDK` → `1pn`,
> `2LDK` → `2pn`, `3LDK` → `3pn`, `4LDK` → `4pn_plus`, `1R`/`1K` → `studio`.
> Hậu tố `+S` (`サービスルーム`, phòng không đủ điều kiện cửa sổ để gọi là phòng
> ngủ) → biến thể `_plus` và `has_multipurpose_room = true`, vd `2LDK+S` → `2pn_plus`.
> Giá căn hộ Nhật thường công bố **theo căn** (`price_unit = per_unit`), không phải
> theo m² — không tự chia ra đơn giá nếu nguồn không nêu.

---

## 11. Nguồn dữ liệu (raw) — gợi ý theo nhóm feature

| Feature | Nguồn ưu tiên | Ghi chú pháp lý/kỹ thuật |
|---|---|---|
| Layout & diện tích phòng (B3) | Brochure PDF của CĐT, trang "floor plan" | **Ảnh** — cần OCR/nhập tay (xem §4.2) |
| Kiến trúc (B1 §2.2) | Trang dự án, trang công ty kiến trúc, ArchDaily, CTBUH | Text tốt, extract regex được |
| Bàn giao (B5) | Brochure, mục "specification"/"tiêu chuẩn bàn giao" | Phân biệt gói chuẩn vs option |
| Tiện ích (B6) | Trang dự án mục "amenities/tiện ích" | Dễ trùng với tiện ích cấp KĐT |
| Giá sơ cấp (B7) | Trang CĐT, bảng hàng sàn phân phối, báo cáo CBRE/JLL/Savills | Giá CĐT hay ẩn sau form đăng ký |
| Giá thứ cấp (B7) | Portal niêm yết: batdongsan.com.vn, Naver 부동산, 贝壳/链家, PropertyGuru, EdgeProp | **Kiểm `robots.txt` trước** — nhiều portal chặn crawl; nếu chặn → dùng báo cáo thị trường thay thế, **không vượt rào** |
| Product mix (B2/B4) | Brochure, trang "mặt bằng tầng" | Mặt bằng tầng cũng thường là ảnh |

---

## 12. Provenance (extractor tự thêm — mỗi trường)

Giữ nguyên chuẩn WS1: `source_url` · `source_file` · `snippet` (câu/ô gốc) ·
`accessed_at` · `confidence` (`high`/`medium`/`low`).

Quy ước `confidence`:

| Mức | Khi nào |
|---|---|
| `high` | Trích từ trang chính thức của CĐT hoặc báo cáo có tên đơn vị + kỳ |
| `medium` | Trang bên thứ ba uy tín (portal lớn, báo ngành, ArchDaily) |
| `low` | OCR bản vẽ chưa xác nhận, giá suy từ tin rao lẻ, giá trị bị che khuất |

---

## 13. Quy tắc chống lỗi (contract)

1. **Luôn ghi cơ sở diện tích.** `area_basis_reported` / `price_basis` là
   `required`. Không quy đổi tim tường ↔ thông thuỷ nếu nguồn không nêu hệ số.
2. **Tách sơ cấp / thứ cấp.** Không gộp một cột `price`. Giá **rao** ≠ giá **giao
   dịch** → phân biệt bằng `source_type`.
3. **VAT & phí bảo trì.** Giá CĐT tại VN thường công bố "chưa VAT, chưa phí bảo
   trì 2%" → chênh ~10–12%. Không có thông tin → `includes_vat = null`, **không
   mặc định false**.
4. **Không bịa số.** Nguồn không nêu → `null`. Cấm nội suy diện tích phòng từ tổng
   diện tích căn, cấm chia đều `num_units_total / num_floors`.
5. **Bảng con là nguồn sự thật.** Trường tóm tắt ở B1 (§1.3, §1.4) luôn **derived**
   từ B2/B4/B5/B6, tính lại mỗi lần chạy, không nhập tay.
6. **Extractor không gọi mạng, idempotent.** Chạy lại trên cùng raw → cùng output.
7. **Raw append-only.** Kế thừa nguyên tắc WS1.
8. **Tôn trọng `robots.txt`.** Đặc biệt với portal niêm yết ở B7; bị chặn thì
   chuyển sang nguồn báo cáo, ghi rõ `source_type = bao_cao_cbre_jll`.
9. **Kiểm tra chéo bắt buộc** (log cảnh báo, không tự sửa):
   - `sum(B3.area_m2) ≤ B2.area_gross_m2`, chênh trong dải 5–15%
   - `count(B3 phòng ngủ) == B2.bedrooms`
   - `sum(B2.num_units_of_type) == B1.num_units_total`
   - `sum(B4.units_per_floor × số tầng trong floor_range) ≈ B1.num_units_total`
   - `sum(B2.share_of_total_pct) ≈ 100`

---

## 14. Output

```
output_raw/<building_id>/          ← raw, append-only
├── pages/            *.html | *.txt | *.png | *.pdf
├── floorplans/       ảnh mặt bằng căn & mặt bằng tầng
├── refer_file/       <building_id>_rooms.csv — vòng người xác nhận B3 (§4.2)
├── sources.json      bảng nguồn agent tự tuyển
├── extract_text.json · extract_floorplan.json   record thô của extractor
├── manifest.json · crawl_log.csv · validation.log

output_csv/<building_id>.csv       ← MỖI TOÀ NHÀ ĐÚNG MỘT FILE
```

### 14.1 Bố cục `output_csv/<building_id>.csv`

**1 dòng = 1 record của một trong 7 bảng.** Bảy bảng xếp chồng trong cùng một file,
phân biệt bằng cột `bang`; cột feature là hợp của cả 7 bảng nên mỗi dòng chỉ điền
những cột thuộc bảng của nó, phần còn lại để trống.

| Nhóm cột | Cột | Ý nghĩa |
|---|---|---|
| Định vị | `bang` | `B1`…`B7` |
| | `bang_ten` | `building`, `unit_type`, `unit_room`, `floor_plate`, `handover_item`, `amenity`, `price_obs` |
| | `record_key` | khoá của bảng tương ứng (`building_id`, `unit_type_id`, `room_id`…) |
| | `record_label` | nhãn người đọc, vd `2PN-A · PN1`, `2PN-A · sơ cấp · 2026-Q1` |
| Feature | hợp toàn bộ `name` của B1…B7 | thứ tự cột theo thứ tự bảng trong spec |
| Evidence | `confidence` | mức thấp nhất trong các trường của record |
| | `source_urls` | các URL nguồn của record, phân tách `; ` |
| | `evidence_json` | `{"<field>": {"url", "file", "snippet", "confidence"}}` — tra được nguồn của **từng trường** |

- Khoá `record_key` trong một file: không rỗng, không trùng.
- Dòng `B1` mang thêm các cột tổng hợp derived (`num_unit_types`, `num_amenities`,
  `price_usd_per_m2_primary/secondary`, `secondary_premium_pct`, `price_growth_pct_yoy`,
  `sources_ok`, `extracted_at`) — thay cho bảng `building_benchmark` cũ.
- Hai trường trùng tên giữa các bảng (`area_m2` ở B3/B6, `source_type` ở B3/B7) dùng
  chung một cột; cột `bang` là thứ phân biệt danh mục giá trị áp dụng.
- So sánh chéo nhiều toà: đọc nhiều file rồi lọc theo `bang`, vd
  `pd.concat(map(pd.read_csv, glob("output_csv/*.csv"))).query("bang == 'B2'")`.

---

## 15. Seed case — **chưa chốt, cần xác nhận**

Danh sách toà nhà mục tiêu chưa được quyết. Gợi ý bám theo 8 KĐT đã benchmark ở WS1
để hai workstream nối được với nhau:

| linked_case_id (WS1) | Ứng viên toà nhà | Trạng thái |
|---|---|---|
| `vinhomes_ocean_park` | phân khu Sapphire / Ruby / Diamond | cần chốt toà cụ thể |
| `vinhomes_smart_city` | Sapphire / Masteri West Heights | cần chốt |
| `ecopark` | Sky Oasis / Aqua Bay | cần chốt |
| `phu_my_hung` | Scenic Valley / Riverpark Premier | cần chốt |
| `incheon_songdo` | các tháp căn hộ Songdo IBD | **cần khảo sát nguồn tiếng Hàn** |
| `hongqiao` | tháp căn hộ Hongqiao CBD | **cần khảo sát nguồn tiếng Trung** |
| `tianfu` | tháp căn hộ Tianfu New Area | **cần khảo sát** |
| `gia_binh_airport_city` | sản phẩm dự kiến GBAC | `is_target = true`, phần lớn trường sẽ `null` |

> **Không đưa tên toà vào registry khi chưa xác minh tồn tại + có nguồn chính thức.**
> Đây là danh sách gợi ý để thảo luận, không phải dữ liệu.

---

## 16. Việc còn lại để chạy được WS2

Theo quy trình "Thêm một workstream mới" ở `README.md`:

| Bước | Trạng thái |
|---|---|
| 1. `features/ws2_building/feature_spec.md` | ✅ file này |
| 2. Bảng nguồn `refer_file/<building>.txt` | ⬜ chờ chốt danh sách toà (§15) |
| 3. `agent_extractor/ws2_building/extractor_skill.md` | ⬜ |
| 4. Sinh `extract_building.py` từ spec | ⬜ |
| 5. Tái dùng `crawl_sources.py` + bổ sung tải/lưu ảnh floorplan | ⬜ |
| 6. Thêm entry `ws2_building` vào `PIPELINES` trong `scripts/run_ws.py` | ⬜ |
| 7. Quy trình nhập/OCR bản vẽ → `refer_file/<building>_rooms.csv` (§4.2) | ⬜ **rủi ro cao nhất** |
