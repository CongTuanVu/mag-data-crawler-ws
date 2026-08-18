# code_extract — bóc tách bằng CODE thay vì gọi LLM từng trang

Đường nhanh cho danh sách dài: **crawl hết trước**, rồi **một lượt agent đọc cấu
trúc HTML để viết trình bóc tách**, sau đó 209 toà chạy bằng code — không token,
không chờ mạng.

```bash
python run.py --input buildings.txt --crawl-only     # [1][2] crawl cả danh sách
python run_extract.py build                          # 1 lượt agent → rules.py
python run_extract.py run --workers 8                # [3][4] → output_csv/
python run_extract.py translate                      # dịch gộp thuật ngữ còn sót
```

Đo trên máy: **~0.15 s/toà** (209 toà ≈ 30 giây) so với 6 lượt gọi model mỗi toà
ở đường cũ. Đường cũ vẫn nguyên vẹn — `python run.py --input buildings.txt` không
đổi hành vi.

## File nào viết tay, file nào agent sinh

| File | Ai viết | Vai trò |
|---|---|---|
| `common.py` | tay, **ổn định** | Parse bảng 物件概要 → nhãn/giá trị, quy đổi ㎡/坪/帖 & 億円/万円, `間取り` → `layout_class`, gom provenance |
| `runner.py` | tay, **ổn định** | Nạp code sinh ra, ép kiểu/enum theo `pipeline/schema.py`, khử trùng, gộp B1, dịch, ghi `extract_text.json` |
| `lexicon.py` | tay (hạt giống) | Từ điển JP→VI + ghi nhận thuật ngữ chưa có |
| `lexicon_auto.json` | bước `translate` | Thuật ngữ do LLM dịch gộp, tra được từ lần chạy sau |
| `rules.py` | **agent sinh** | Quy tắc chung theo nhãn — gánh phần lớn dữ liệu |
| `sites/*.py` | **agent sinh** | Override cho cổng có cấu trúc riêng (suumo, homes, major7…) |

`build` **ghi đè** `rules.py` + `sites/*.py`; bản cũ luôn được sao lưu ở
`.bak/<YYYYmmdd_HHMMSS>/` trước khi ghi.

## Hợp đồng cho code sinh ra

```python
# rules.py — chạy cho mọi site
def building(pages, ctx) -> list[dict] | {"records": [...], "notes": "..."}
def unit_type(pages, ctx); def floor_plate(pages, ctx); def handover_item(pages, ctx)
def amenity(pages, ctx);   def price_obs(pages, ctx)

# sites/suumo_jp.py — override một cổng, nhận TỪNG trang
HOSTS = ("suumo.jp",)
def unit_type(page, ctx) -> list[dict]
```

Record của `sites/` được ưu tiên khi trùng khoá; `rules.py` lấp chỗ trống.
Runner tự lo: ép kiểu, loại giá trị ngoài enum, bù trường thiếu bằng `None`,
khử trùng, gộp B1 về đúng 1 dòng, dịch JP→VI, ghi file. Hàm nào ném lỗi thì chỉ
bảng đó rỗng kèm cảnh báo — không giết cả mẻ.

## Vì sao bảng vẫn rỗng ở vài toà

Đó là **có chủ ý**, không phải hỏng:

- **B2 `unit_type`** — nhiều CĐT Nhật chỉ công bố *dải* (`2LDK~3LDK`,
  `58.13㎡~83.68㎡`) chứ không có bảng từng loại căn. Suy ngược ra từng type là
  bịa (feature_spec quy tắc 1) → để trống, ghi lý do vào `notes`.
- **B3 `unit_room`** — chỉ đọc được từ ảnh bản vẽ, là bước vision:
  `python run.py --input buildings.txt --skip-discover --skip-crawl --skip-extract`
  (dùng lại `extract_text.json` do code sinh, chỉ chạy vision rồi ghi CSV lại).
- **B4 `floor_plate`** — hầu như không tồn tại ở dạng text trong nguồn Nhật.

## Dịch sang tiếng Việt

Code không dịch được. Mọi trường mô tả đi qua `lexicon.vi_phrase()`; trượt từ
điển thì **giữ nguyên văn** và ghi vào `.lexicon_misses.json`. Chạy
`python run_extract.py translate` để gom toàn bộ term của cả mẻ thành 1–2 lượt
LLM dịch batch (250 term/lượt) rồi ghi vào `lexicon_auto.json` — lần sau tra
được ngay, chi phí tiến dần về 0. Xem trước bằng `translate --dry-run`.

Trường `*_local`, `*_code`, `*_url`, và `snippet` trong provenance **không bao
giờ** bị dịch (feature_spec quy tắc 8c).

## Sửa tay hay sinh lại?

Cả hai đều được — `rules.py` là Python thường. Vòng lặp gọn:

```bash
python run_extract.py check <building_id> --table unit_type   # xem trực tiếp, không ghi CSV
python run_extract.py survey --out /tmp/survey.txt            # đúng thứ agent sẽ đọc
```

Sửa tay rồi `check` lại là đủ; `build` chỉ cần khi thêm nhiều nguồn mới có cấu
trúc lạ. Biến môi trường điều chỉnh bước sinh: `WS1_CODEGEN_MAX_TOKENS` (48000),
`WS1_CODEGEN_HOSTS` (22 domain lặp lại), `WS1_CODEGEN_ONEOFF` (14 site CĐT riêng),
`WS1_CODEGEN_CHARS` (trần 420k ký tự bản khảo sát).
