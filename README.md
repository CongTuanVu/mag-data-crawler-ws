# WS1 Building — pipeline agent

Từ **một dòng tên toà nhà** → **7 bảng feature CSV** theo
[`features/ws1_building/feature_spec.md`](features/ws1_building/feature_spec.md).

Agent tự tìm nguồn trên mạng (không cần soạn tay bảng nguồn), crawl raw về
`output_raw/`, trích feature bằng structured output + đọc ảnh mặt bằng bằng
vision, rồi ghi `output_csv/`.

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...        # hoặc tạo file .env

python3 run.py "Marina One Residences, Singapore"
python3 run.py --input buildings.txt
```

## Luồng

```
tên toà nhà
   │
   ├─[1] discover.py   Claude + web_search/web_fetch tự xác minh toà nhà và
   │                   tuyển 12–25 URL (tìm cả bằng ngôn ngữ bản địa)
   │                                        → output_raw/<id>/sources.json
   ├─[2] crawl.py      Playwright render JS · bóc PDF · tải ảnh mặt bằng
   │                   tôn trọng robots.txt · raw append-only
   │                                        → output_raw/<id>/{pages,floorplans}/
   ├─[3] extract.py    corpus raw nạp 1 lần vào system prompt có prompt-cache,
   │     floorplan.py  6 lượt structured output (B1,B2,B4,B5,B6,B7)
   │                   + vision đọc bản vẽ → B3 unit_room
   │                                        → output_raw/<id>/extract_*.json
   └─[4] assemble.py   sinh khoá & FK, tính cột derived
         validate.py   kiểm tra chéo §4.1/§13.9 (chỉ cảnh báo, không sửa số)
         writer.py                          → output_csv/<building_id>.csv
```

## Output — mỗi toà nhà đúng một file CSV

`output_csv/<building_id>.csv`, **1 dòng = 1 record**, bảy bảng xếp chồng trong
cùng file và phân biệt bằng cột `bang` (spec §14.1):

| Nhóm cột | Cột | Nội dung |
|---|---|---|
| Định vị | `bang` · `bang_ten` | `B1 building` … `B7 price_obs` |
| | `record_key` | khoá của bảng đó (`unit_type_id`, `room_id`, `price_id`…) |
| | `record_label` | nhãn người đọc, vd `2PN-A · PN1` |
| Feature | hợp toàn bộ trường B1–B7 (~155 cột) | mỗi dòng chỉ điền cột thuộc bảng của nó |
| Evidence | `confidence` | mức thấp nhất trong các trường của record |
| | `source_urls` | URL nguồn của record |
| | `evidence_json` | `{"<field>": {"url","file","snippet","confidence"}}` — tra nguồn **từng trường** |

Dòng `B1` mang thêm cột tổng hợp (`num_unit_types`, `amenity_count`,
`price_usd_per_m2_primary/secondary`, `secondary_premium_pct`,
`price_growth_pct_yoy`, `sources_ok`, `extracted_at`).

File ghi UTF-8 có BOM, mở thẳng bằng Excel không vỡ tiếng Việt. Đối chiếu nhiều toà:

```python
import glob, pandas as pd
df = pd.concat(map(pd.read_csv, glob.glob("output_csv/*.csv")))
df.query("bang == 'B2'")[["record_label", "area_gross_m2", "bedrooms", "source_urls"]]
```

## Cờ hay dùng

| Cờ | Tác dụng |
|---|---|
| `--linked-case-id incheon_songdo` | FK sang `case_benchmark.case_id` của WS1 khu đô thị |
| `--target` | đánh dấu `is_target = true` (sản phẩm GBAC) |
| `--max-floorplans 8` | giới hạn số ảnh mặt bằng đọc bằng vision (mỗi ảnh 1 lượt gọi) |
| `--skip-discover / --skip-crawl / --skip-extract / --skip-vision` | dùng lại kết quả bước trước |
| `--fresh` | bỏ raw cũ, crawl lại từ đầu (mặc định là APPEND) |
| `--headful`, `--no-shots`, `--timeout N` | điều khiển trình duyệt |
| `--dry-run` | in danh sách toà nhà đọc được từ input rồi dừng, không gọi API |
| `--check-spec` | đối chiếu `feature_spec.md` ↔ `schema.py`, thoát mã 1 nếu lệch |

Input nhận **mỗi dòng một tên toà**, hoặc **bảng markdown** (tự nhận cột
`Toà nhà`/`Building`/`Tên` và ghép thêm cột `Thành phố`/`City` cho đỡ nhầm toà).

## Sửa spec thì chạy `--check-spec`

`schema.py` là bản dịch tay của spec sang JSON Schema: agent đọc spec để hiểu ý
nghĩa từng trường, nhưng **bộ trường được phép trả về** do `schema.py` định nghĩa.
Thêm trường vào spec mà quên sửa `schema.py` thì trường đó bị bỏ im lặng.

```bash
python3 run.py --check-spec
```

Đối chiếu từng trường của 7 bảng (mục 1–8) và từng giá trị của 18 danh mục (§9):

```
  B2 unit_type              21    20   THIẾU 1
  §9 status                  5     4   lệch 1

✗ 2 chênh lệch:
  - [THIẾU] B2 unit_type: spec khai `ceiling_height_m` nhưng schema.py không có
            → trường này sẽ bị bỏ im lặng
  - [VOCAB] `status`: spec có giá trị `tam_dung` mà schema.py thiếu
            → model không được phép trả giá trị này
```

Phần văn xuôi (§10 quy đổi đơn vị, §13 quy tắc chống lỗi, §14 output) không cần
đối chiếu — agent đọc thẳng từ file spec mỗi lần chạy.

Biến môi trường: `WS1_MODEL` (mặc định `claude-opus-5`), `WS1_EFFORT`
(`low|medium|high|xhigh|max`, mặc định `high`), `WS1_MAX_TOKENS`.

## Vòng người xác nhận cho B3 (spec §4.2)

Diện tích từng phòng chỉ tồn tại trong ảnh bản vẽ, nên mọi dòng B3 do vision sinh
ra đều `source_type = floorplan_image`, `confidence = low`. Sau mỗi lần chạy,
pipeline ghi `output_raw/<id>/refer_file/<id>_rooms.csv` có cột `verified` để
trống. Điền `yes` (sửa số nếu cần) rồi chạy lại → dòng đã xác nhận thay kết quả
vision và lên `confidence = high`, `source_type = manual`.

## Nguyên tắc dữ liệu (kế thừa feature_spec §13)

- **Không bịa số.** Nguồn không nêu → `null`. Extractor bị cấm dùng kiến thức nền
  ngoài corpus đã crawl; cấm nội suy diện tích phòng, cấm chia đều số căn/tầng.
- **Luôn ghi cơ sở diện tích/giá.** `area_basis_reported`, `price_basis` bắt buộc;
  không tự quy đổi tim tường ↔ thông thuỷ.
- **Tách sơ cấp / thứ cấp.** Không gộp một cột `price`; VAT không nêu → `null`.
- **Bảng con là nguồn sự thật.** Cột tóm tắt ở B1 (§1.3–§1.4) tính lại mỗi lần chạy.
- **Raw append-only**, **tôn trọng `robots.txt`** — URL bị chặn ghi
  `status = robots_blocked` trong `crawl_log.csv`, không vượt rào.
- Kiểm tra chéo §13.9 chạy mỗi lần và ghi `output_raw/<id>/validation.log`;
  **chỉ cảnh báo, không tự sửa số**.

Hai chỗ lệch spec, có chủ ý:
`handover_standard` trích từ nguồn (kèm provenance) thay vì suy từ B5 — B5 là danh
sách hạng mục, không suy ra được mức bàn giao chủ đạo; `price_usd_per_m2` dùng
bảng tỷ giá tĩnh trong `pipeline/config.py` (`FX_DATE`), sửa tay khi cần.

---

`mag_agent.py` là prototype đơn giản trước đó (crawl Wikipedia → `output_csv/features.csv`,
schema rút gọn ở `features/ws1_building/feature_spec.md`). Giữ nguyên, không dùng
chung file output với pipeline này.
