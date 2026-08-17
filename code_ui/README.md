# code_ui — trang HTML xem dữ liệu toà nhà

Dựng một file HTML **self-contained** (không cần server, không CDN) từ các CSV
trong [output_csv/](../output_csv/).

## Chạy

```bash
python3 code_ui/build_html.py
open code_ui/dist/index.html
```

Không cần cài gì thêm — chỉ dùng thư viện chuẩn của Python.

Tuỳ chọn:

```bash
python3 code_ui/build_html.py --csv-dir output_csv --out code_ui/dist/index.html
```

## Cấu trúc

| File | Vai trò |
|---|---|
| [build_html.py](build_html.py) | Đọc CSV long-format → gom theo `building_id` → nhúng JSON vào template |
| [template.html](template.html) | Giao diện + CSS + JS; chứa placeholder `__DATA__` |
| `dist/index.html` | Kết quả (bị git ignore, build lại bất cứ lúc nào) |

## Trang hiển thị gì

CSV ở dạng long: mỗi dòng là một bản ghi của một bảng, phân biệt qua cột `bang`.
Trang tách lại thành các khối theo đúng mô hình dữ liệu của
[feature_spec.md](../features/ws1_building/feature_spec.md):

- **Tổng quan** — tên, tên bản địa, CĐT, địa chỉ, badge loại hình/phân khúc/độ tin cậy,
  dải chỉ số (tầng, số căn, GFA, mật độ, hệ số sử dụng…). Ô rỗng tự ẩn.
- **Kiến trúc & thiết kế** (B1 §2.2) — KTS, concept, từ khoá, mặt đứng, điểm nhấn.
- **Loại căn hộ** (B2) — bảng mã căn, layout, PN/WC, diện tích thông thuỷ/tim tường, tỷ trọng.
- **Layout phòng** (B3) và **Mặt bằng tầng** (B4) — chỉ hiện khi CSV có dữ liệu.
- **Tiêu chuẩn bàn giao** (B5) — nhóm theo `item_category`, kèm spec + thương hiệu.
- **Tiện ích nội khu** (B6) — nhóm theo `amenity_category`, tiện ích nổi bật đánh dấu ★.
- **Giá** (B7) — sơ cấp/thứ cấp, min–max–avg, quy đổi ≈ USD theo `fx_rate_to_usd`, link nguồn.
- **Nguồn dữ liệu** — gom `source_urls` của cả toà và các bảng con.

Ngoài ra: ô **tìm kiếm** (quét cả tên tiện ích / hạng mục bàn giao / mã căn),
tab **So sánh** dựng bảng đối chiếu mọi toà theo ~25 tiêu chí, và nút đổi
giao diện sáng/tối.

## Lọc & sắp xếp

Thanh dưới header có 2 bộ điều khiển, áp dụng cho cả tab Chi tiết lẫn So sánh:

- **Quốc gia** — dựng từ chính giá trị `country` trong CSV, kèm số lượng:
  `Tất cả (6)` · `Japan (1)` · `Chưa rõ (5)`. Bản ghi thiếu `country` gom vào
  nhóm "Chưa rõ" và thanh lọc hiện cảnh báo số lượng — đây là **thiếu dữ liệu ở
  khâu trích xuất**, không phải lỗi UI; sửa ở pipeline thì web tự đúng.
- **Sắp xếp** — mặc định `Đầy đủ nhất`; ngoài ra Tên A→Z, Số căn, Chiều cao,
  Năm bàn giao, Số tiện ích. Mọi kiểu sắp xếp đều lấy điểm đầy đủ làm tiêu chí
  phá hoà, rồi mới đến tên.

### Điểm "độ đầy đủ"

Thang 0–100, hiện dưới dạng thanh màu ở mỗi mục sidebar, badge ở đầu trang chi
tiết và một dòng trong bảng So sánh. Rê chuột để xem chi tiết cách tính:

| Thành phần | Trọng số | Cách tính |
|---|---|---|
| Trường trọng yếu B1 | 45% | tỷ lệ lấp đầy trên 34 trường (`B1_KEY_FIELDS`) |
| Độ phủ bảng con | 35% | so với ngưỡng kỳ vọng: `unit_types` 8, `handover_items` 20, `amenities` 15, `prices` 4, `rooms` 10, `floor_plates` 1 |
| Độ tin cậy | 20% | `high` 1.0 · `medium` 0.6 · `low` 0.25 |

Đây là thước đo **mức độ hoàn thiện của bản ghi**, không phải chất lượng nội dung.
Muốn đổi trọng số / ngưỡng thì sửa `B1_KEY_FIELDS`, `CHILD_TARGETS`, `CONF_WEIGHT`
trong [template.html](template.html).

## Phân trang

Mỗi file CSV trong `output_csv/` = 1 toà trên web, **tất cả đều lên trang**, chia trang
để danh sách không dài vô tận khi số toà tăng:

- **Danh sách toà** (sidebar) — mặc định 8 toà/trang; chọn lại 8 / 16 / 32 / Tất cả.
- **Tab So sánh** — phân trang theo **cột**, mặc định 6 toà/trang (4 / 6 / 10 / Tất cả),
  vì bảng nhiều cột quá thì cuộn ngang không đọc nổi.

Thanh phân trang hiện `3–8 / 47 toà`; quá 7 trang thì rút gọn dạng `1 … 4 5 6 … 12`.
Tìm kiếm sẽ đưa về trang đầu; toà đang xem vẫn giữ nguyên ở panel chi tiết khi lật trang.

## Thêm toà mới

Chạy pipeline sinh CSV vào `output_csv/` rồi build lại — trang tự nhận file mới,
không cần sửa code. File nào thiếu dòng `B1 building` sẽ bị bỏ qua kèm cảnh báo.
