# code_ui — trang HTML xem dữ liệu toà nhà

Dựng file HTML **self-contained** (không cần server, không CDN) từ các CSV
trong [output_csv/](../output_csv/).

Có **hai trang độc lập**, hai nguồn khác nhau, không chia sẻ template:

| Trang | Nguồn | Hình thái dữ liệu |
|---|---|---|
| `dist/index.html` | `output_csv/*.csv` (WS1, 161 cột) | sâu — 1 toà nhà, 7 bảng B1–B7 |
| `dist/lan.html` | `output_csv/file_lan.csv` (60 cột) | rộng — 66k khu × 15 thị trường, mỗi khu 1 dòng |

Hai tập dữ liệu **không dùng chung schema** (không một tên cột nào trùng nhau),
nên mỗi bên có builder và template riêng. Xem [§ Trang benchmark](#trang-benchmark-lanhtml).

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


## Trang benchmark (`lan.html`)

```bash
python3 code_ui/build_lan_html.py
open code_ui/dist/lan.html
```

Tuỳ chọn: `--csv <đường dẫn>` · `--sample <n>` (số dòng nhúng vào bảng tra cứu,
mặc định 1500) · `--out <đường dẫn>`.

| File | Vai trò |
|---|---|
| [build_lan_html.py](build_lan_html.py) | Tổng hợp toàn bộ 66k bản ghi → nhúng thống kê + mẫu bảng |
| [template_lan.html](template_lan.html) | Giao diện riêng cho dữ liệu rộng-nông |

### Vì sao không tái dùng `template.html`

`template.html` là trang chi tiết từng toà: nó trông đợi các bảng con B2–B7 và
chấm điểm độ đầy đủ theo 34 trường B1. Dữ liệu `file_lan` chỉ phủ ~35% số trường
đó, nên mọi toà sẽ nhận điểm ~21/100 và trang chi tiết gần như trắng. Ngược lại
`file_lan` có thứ `template.html` không biết hiển thị: 15 thị trường để so sánh
ngang, và cột `*_basis` cho biết giá trị nào là đo thật.

### Ba ràng buộc của tập dữ liệu, đã mã hoá vào trang

1. **`mix` mang ba ngữ nghĩa khác nhau** tuỳ `mix_kind`, không bao giờ cộng gộp:
   - `br_types_only` (77,7%) — `[{br, area_min_m2, area_max_m2}]`, loại căn theo số PN
   - `br_counts` (1,5%) — `[{br, n_units}]`, kèm số căn
   - `area_bands` (20,8%) — `[f,f,f,f]`, tỷ lệ theo bốn dải diện tích

   Hai biểu đồ tách riêng, tiêu đề nói rõ là hai đại lượng khác nhau.

2. **Giá không có tỷ giá.** Mỗi thị trường một đơn vị bản địa (`wan/ping`,
   `manwon/m2`, `THB`, `BRL`…). Trang không bao giờ đặt giá của hai thị trường
   lên chung một trục — phần "Khoảng giá" là small multiples, mỗi thẻ một thang riêng.

3. **"Đầy 100%" không có nghĩa là đã đo.** `style` 68,6% `derived`,
   `handover` 69,1% `policy`. Khối "Nguồn gốc dữ liệu" đứng trên mọi biểu đồ và
   mỗi dòng trong bảng tra cứu đều gắn nhãn `measured` / `derived` / `policy`.

### Quy mô

Thống kê tính trên **đủ 66.043 bản ghi**; chỉ bảng tra cứu là mẫu (phân tầng theo
thị trường, ưu tiên khu nhiều căn), số dòng bị lược ghi rõ ngay trên bảng. Nhúng
cả 66k dòng sẽ ra file ~91 MB — trình duyệt không chịu nổi.

### Màu biểu đồ

Ramp thứ bậc 1 sắc (blue, 5 bậc) cho cơ cấu PN và dải diện tích; bộ status
xanh/vàng/đỏ cho nguồn gốc dữ liệu, luôn kèm icon + nhãn chứ không dựa vào màu.
Cả hai bộ đã chạy qua `validate_palette.js` ở cả nền sáng và nền tối
(`--ordinal`, surface `#ffffff` / `#171b21`) — đạt toàn bộ kiểm tra. Mỗi biểu đồ
có nút **Bảng** để đọc số trực tiếp, không phụ thuộc tooltip.
