# Nguồn tự cấp theo từng toà

Một file cho mỗi toà, đặt tên `<building_id>.txt` — cùng slug với
`output_csv/<building_id>.csv`. Dùng bằng:

    python3 run.py --input buildings.txt --sources-dir sources

Kiểm tra toà nào còn thiếu file trước khi chạy thật:

    python3 run.py --input buildings.txt --sources-dir sources --dry-run

Mỗi dòng trong file: `URL [| purpose [| ghi chú]]`, dòng `#` là chú thích.

purpose hợp lệ: official_overview · floorplan · brochure_pdf · amenities ·
handover_spec · product_mix · price_primary · price_secondary · architecture ·
news_report · market_report
