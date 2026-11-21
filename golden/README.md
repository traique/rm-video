# Bộ video mẫu (golden set)

Chép 5–10 video nguồn **thật** (đủ kiểu: có lời nhiều, ít lời, có phụ đề in cứng, nguồn 720p và 1080p, nấu ăn và
review) vào `golden/videos/`. Muốn đặt chế độ/tiêu đề thì chép `manifest.example.json` thành `manifest.json` rồi sửa.

```
python golden.py run        # chạy cả bộ (mỗi video ~2-3 phút trên máy 1 lõi), ghi golden/report.md
python golden.py baseline   # lần chạy vừa rồi đạt ý -> lấy làm chuẩn
python golden.py compare    # so lần chạy gần nhất với chuẩn
```

Trên Botkeep: gõ `golden`, `golden baseline` hoặc `golden compare` vào console (chạy nền, ưu tiên CPU thấp, chỉ khi
hàng đợi trống).

Mỗi lần sửa code / đổi model / đổi giọng: `run` rồi xem `report.md`. ❌ là tụt rõ (âm lượng sai, nguyên liệu bị mất
hoặc bịa nhiều hơn, độ nét giảm mạnh, lỗi chạy); ⚠️ là nên xem lại bằng mắt (`golden/out/<tên>_khung.jpg`,
`<tên>_bia.jpg`). Video, kết quả và chuẩn không đưa lên git (xem `.gitignore`).
