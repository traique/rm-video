# Kế hoạch: remake theo phong cách của kênh (bản 42)

Mục tiêu: video làm lại nhìn vào là biết của kênh bạn, nhịp dựng khác hẳn video gốc, video nấu ăn chỉ đảo thứ tự ở
chỗ hợp lý, bài đăng có công thức và mẹo, mọi định lượng đều đối chiếu được với video gốc. Nguồn: nhiều kênh Facebook
và TikTok.

## Trạng thái thực hiện (16/11/2026)

Đã làm S0-S6 (mỗi giai đoạn một commit trên nhánh). Giọng văn mặc định đổi sang miền Nam theo yêu cầu.

| Giai đoạn | Mã | Ghi chú lệch so với kế hoạch |
| --- | --- | --- |
| S0 | `rmvideo/app/settings.py` (nhóm ✨ style, 🎨 brand), `Job.edit` / `Job.recipe` | thêm `AUTO_SKIP_ADS` để tắt được bộ lọc quảng cáo |
| S1 | `rmvideo/domain/recipe.py`, `rmvideo/app/flow/dish.py`, `prompt_engine.recipe_facts` | câu "cách làm" có số lạ được MÃ thay "vừa đủ" / "một lúc" (không tốn thêm lượt AI viết lại); AI đọc công thức chung một lượt với nhãn bước + mẹo |
| S2 | `filtergraph.LOOKS`, `subs.style_line`, `renderer` (màn kết, âm hiệu, thẻ), `cover` (mẫu kênh), `rmvideo/app/brand.py` | màn kết giữ nền tối + tên + tagline (chưa dừng hình cảnh món) |
| S3 | `rmvideo/media/edl.py`, `rmvideo/app/flow/style.py`, `Remake.restyle` | dải che chữ in cứng: che hợp của mọi cỡ khung sau khi nối (một lớp, không che từng khúc) |
| S4 | `rmvideo/app/flow/style_b.py` | B7 / B8 (thẻ giá, kết luận cho review) chưa làm vì chưa có bước hiểu nội dung cho review: review `strong` = `light` |
| S5 | `rmvideo/app/flow/dedupe.py`, lọc `AUTO_MIN_HEIGHT` / quảng cáo trong `autopost` | chế độ theo từng nguồn hoãn (hiện một nguồn nấu ăn) |
| S6 | thẻ «MẸO» (`Remake.tip_card`, `renderer.overlay_filter`), README, CHANGELOG | mẹo chưa chen vào lời đọc (chỉ thẻ + bài đăng) |
| S7 | — | hoãn đúng kế hoạch (chưa dùng nguồn nước ngoài) |
| Bản ngắn | `rmvideo/app/flow/short.py`, `Remake.render_short`, `dispatch.run_job` (giao sau bản đầy đủ), `autopost` (`AUTO_POST_VERSION`) | thêm theo yêu cầu 16/11: mỗi bước chính ≥ 1 câu, đúng thứ tự, không lấy câu tán gẫu; lời đọc của bản ngắn AI viết sau khi duyệt bản đầy đủ (không qua màn duyệt) |

Chạy thử trên video thật của nguồn: cánh gà chiên nước mắm, 277 giây, có phụ đề tiếng Việt của TikTok làm mốc lời.
- Đo chuyển động: ~8 giây trên 4 lõi.
- Lớp A: 42 khúc, đổi cỡ khung 38 lần, tua 2 đoạn chờ (13s → 4s), 277s → 266s.
- Dựng thật 60 giây đầu (720p, tông tươi sáng, màu kênh, thẻ nguyên liệu + mẹo, màn kết + tagline): 22 giây trên 4 lõi.
- Đầu bếp nói gần như liên tục nên lớp A rút ngắn ít; khác biệt chính là khung hình và thẻ. Lớp B (bỏ lời tán gẫu,
  điểm danh, tóm tắt) cần AI đọc công thức nên chưa chạy thật được ở đây (không có key Gemini trong môi trường này).

## 0. Đã chốt với chủ dự án (16/11/2026)

| # | Nội dung | Chốt |
|---|---|---|
| 1 | Lớp A (đổi nhịp, khung hình, màu) và lớp B (đảo thứ tự có kiểm soát) | Làm cả hai |
| 2 | Tông màu, kiểu chuyển cảnh | **Cố định theo kênh** (bộ nhận diện kênh). Chỉ nhịp dựng thay đổi theo từng video. Kênh MỚI xây từ đầu, tên / màu / tông chưa chốt (gợi ý ở §11) |
| 3 | Chữ "15 phút sau" ở đoạn tua nhanh | `auto`: có thời gian thật thì "⏩ 30 PHÚT SAU", không thì chỉ ⏩ (§3.4) |
| 4 | `ALLOWED_SOURCES` | Không dùng. Để trống là nhận mọi kênh (mã hiện tại đã vậy, `rmvideo/app/flow/common.py:32`), không cần sửa |
| 5 | Mẹo hay, công thức trong bài đăng | Có |
| 6 | Đối chiếu định lượng với lời gốc | Có, là bước bắt buộc trước khi in số lên video hoặc bài đăng |
| 7 | Nguồn | Nhiều kênh Facebook và TikTok |
| 8 | Định lượng không đối chiếu được | Bỏ số, ghi "vừa đủ" (`QTY_CHECK=chặt`) |
| 9 | Nguồn nước ngoài | Đổi HẲN sang đơn vị chuẩn Việt Nam, mã tính lại số (§3.2) |
| 10 | Nhạc nền của video gốc | Giữ như hiện tại: không làm phần thay nhạc |
| 11 | Nguồn đang dùng | Bảy Cua Quán Biên Hòa: facebook.com/bay.cua.quan, tiktok.com/@baycuaquanbienhoa. Chưa dùng video nước ngoài (§1b) |
| 12 | Bộ video vàng | Lấy từ chính kênh này (§1b), bạn không cần gửi thêm link |

## 1. Hiện trạng liên quan (đọc từ mã bản 41)

**Đường chính** (`rmvideo/app/flow/remake.py`, `Remake.run`):

1. `measure`
2. `analyze`: Kaggle bóc lời Whisper, điểm chuyển cảnh, dải chữ, ảnh 2 khung/giây.
3. `place`, `cut_promo`, `plan_separation`
4. `decide_teaser`, `apply_teaser`: 3 giây cảnh món làm đoạn mở đầu. Đây thực chất là một "kế hoạch dựng" 2 khúc.
5. `choose_backend`
6. `write_script`: AI viết lời theo mốc lời gốc và nhìn ảnh từng đoạn.
7. `apply_script`: bài đăng = `platform_post(title, caption 2–3 câu, hashtags, credit)`.
8. Dừng chờ duyệt nếu có.
9. `pre_shorten`
10. `render_remote` / `render_local`: cả hai qua `assemble`, rồi `renderer.render` và `filtergraph.build_filter`.

**Đã có, sẽ dùng lại:**
- **Kiểm nguyên liệu và con số trong lời mới so với lời gốc:** `ingredients.check_rewrite` và `ingredients.check_numbers`. Lệch thì bắt AI viết lại (`prompt_engine.py:721`).
- **Luật giữ định lượng nguyên văn** trong lời nhắc.
- **Thương hiệu hiện có:**
  - `BRAND_NAME`: màn kết 2,2 giây;
  - `BRAND_LOGO`: logo góc phải;
  - `BRAND_INTRO`: chữ hook 3 giây;
  - `SUB_STYLE`: phụ đề karaoke, màu vàng cứng `&H0000E1FF` (`subs.py:6`);
  - `COVER`: ảnh bìa.
- **Tự động đăng nhiều nguồn:** `AUTO_SOURCES` (Facebook), `AUTO_TIKTOK_SOURCES`. Chống trùng bằng độ dài và chú thích giống nhau (`autopost.py:286`).
- **Bộ hiệu ứng nhịp của chế độ 📸:** `trendfx.py`, với `_windows`, `_decay` và `video_fx`.
- **Bundle Kaggle tự đủ:** có kiểm tự động, gồm cả Python 3.10.

**Chưa có:**
- kế hoạch dựng tổng quát (cắt, đảo, lặp cảnh, đổi tốc độ từng khúc);
- màu và tông riêng của kênh: `look_chain` đang là MỘT công thức cố định;
- công thức và mẹo trong bài đăng;
- đối chiếu định lượng từ chữ in trên hình và từ mô tả bài gốc;
- đối chiếu định lượng cho nguồn tiếng Trung hoặc tiếng Anh: `ingredients.numbers` chỉ hiểu số và đơn vị tiếng Việt, nên "200克" hay "2 tbsp" hiện lọt qua không bị kiểm.

**Lưu ý tiếng nền:**
- Model tách giọng Kim_Vocal_2 chỉ chia "giọng người" và "phần còn lại".
- Vì vậy **nhạc nền của video gốc vẫn nằm trong lớp tiếng nền** được trộn vào video mới.
- Video FB/TikTok rất hay lót nhạc thịnh hành. Nhạc đó vừa làm video giống bản gốc, vừa là thứ Facebook hay đối chiếu bản quyền.
- Đã chốt giữ như hiện tại (§3.10). Nếu sau này Facebook hay tắt tiếng hoặc chặn video vì nhạc thì xem lại.

## 1b. Nguồn thực tế: Bảy Cua Quán Biên Hòa (xem 30 video TikTok mới nhất, 16/11/2026)

**Đặc điểm nguồn:**
- **22/30 là video công thức**, phần lớn dài 2–5 phút (126–277 giây), một video 463 giây. Đầu bếp nói tiếng Việt suốt video ("âm thanh gốc"), nguồn 1080x1920.
- **8/30 là video quảng cáo hoặc không có tiêu đề**, dài 16–123 giây: "Khóa tiệc cưới tháng 12", "Khóa bếp ốc lẩu nướng hải sản", "Tiệc trọn gói tại tư gia 120 khách", "học viên ra trường".
  - Tự động đăng hiện chỉ lọc độ dài 5–300 giây, nên có thể bốc trúng các video này rồi làm thành một video quảng cáo khóa học có giọng đọc mới.
- **Mô tả bài gốc KHÔNG có định lượng**, chỉ có tên món và câu khen.
- **Xem thử khung hình một video** (mực nhúng giấm, 130 giây):
  - không có chữ định lượng in trên hình;
  - máy quay đặt cố định trên mặt bếp inox, cùng một góc ở mọi video.
- **Độ dài video ra:** video 90–300 giây hiện được làm NGUYÊN video (`long_mode=full`, ≤ 5 phút) và đăng dạng video thường (`/videos`, không phải Reels). Video > 300 giây bị bỏ qua.

**Hệ quả cho kế hoạch:**
1. **Bằng chứng định lượng gần như chỉ có lời đầu bếp nói.**
   - S1 làm đường `speech` (và `desc` cho đủ).
   - **Hoãn:** đọc chéo chữ trên hình, bộ đọc số tiếng Trung / Anh, và các đơn vị cup / oz / °F. Chuyển sang S7, chỉ làm khi bắt đầu dùng nguồn nước ngoài hoặc nguồn có chữ định lượng trên hình.
   - Vẫn làm phần quy về đơn vị chuẩn trong tiếng Việt (lạng → g, ký / cân → kg) và cộng dồn.
2. **Lọc video quảng cáo đưa lên S1**, vì đang ảnh hưởng tự động đăng ngay bây giờ. Bỏ qua (đánh dấu không dùng nữa) khi:
   - tiêu đề có các cụm "khóa học", "khóa bếp", "học viên", "tiệc trọn gói", "đặt tiệc", "tuyển";
   - hoặc dưới 60 giây mà tiêu đề trống;
   - hoặc AI hiểu món trả `is_recipe=false`.
3. **Lời quảng cáo chen giữa video công thức** ("khóa học", "học viên", "liên hệ quán", số điện thoại, "nhận đặt tiệc"):
   - thêm các cụm này vào bộ nhận lời quảng cáo hiện có (`_PROMO_STRONG` / `_PROMO_WEAK`);
   - kiểm bằng lời bóc thật của kênh.
4. **Lớp A có giá trị lớn với nguồn này:** máy quay cố định một góc, nên đổi cỡ khung luân phiên tạo cảm giác quay nhiều máy, khác hẳn bản gốc.
   - Bỏ thời gian chết ít tác dụng hơn vì đầu bếp nói gần như liên tục.
   - Phần rút gọn chính đến từ B9 (bỏ lời tán gẫu lúc chờ, §3.5) và cắt quảng cáo.
5. **Lớp B hợp:** đầu video thường bày sẵn nguyên liệu trên dĩa (mực, ớt, hành), nên điểm danh nguyên liệu (B2) làm được.
6. **Chống trùng:** cùng một kênh đăng cùng video lên cả Facebook lẫn TikTok, chú thích giống nhau, nên cách so độ dài + chú thích hiện có đã bắt được. Dấu vân hình (S5) hạ ưu tiên.
7. **Bộ video vàng** chọn sẵn từ kênh:
   - cánh gà chiên nước mắm (277 giây);
   - cua đồng chiên nước mắm (152 giây);
   - mực nhúng giấm (126 giây);
   - bò lúc lắc khoai tây (225 giây);
   - luộc gà (148 giây);
   - súp cua (249 giây);
   - và 1 video quảng cáo ("Khóa tiệc cưới tháng 12", 17 giây) để kiểm bộ lọc.

**Lưu ý quyền với nguồn:**
- Toàn bộ video của kênh mới sẽ làm lại từ MỘT kênh. Kênh này là doanh nghiệp (quán, lớp dạy nấu ăn), có trang hoạt động thường xuyên.
- Vì vậy nếu bạn không phải người của Bảy Cua Quán, khả năng bị khiếu nại dồn vào một chỗ.
- **Cách gọn nhất là xin phép hoặc hợp tác.** Nhiều quán đồng ý nếu bài đăng ghi "Công thức: Bảy Cua Quán Biên Hòa" kèm link, vì có lợi cho lớp học của họ. Khi đó bật `SOURCE_CREDIT`.

## 2. Luồng mới

```
measure → analyze → place / cut_promo / separation
  → [MỚI] hiểu món: RecipeFacts (nguyên liệu, định lượng kèm bằng chứng, các bước có nhãn)
  → [MỚI] đối chiếu định lượng (mã kiểm, AI đọc chéo chữ trên hình)
  → [MỚI] lập kế hoạch dựng EditPlan (lớp A, lớp B) → [MỚI] kiểm luật cứng → lỗi thì lùi về A, rồi về tắt
  → write_script trên TRỤC THỜI GIAN MỚI (đoạn nguyên liệu, tóm tắt, mẹo, câu chốt của kênh)
  → kiểm lời (luật cũ + định lượng phải thuộc bảng đã đối chiếu)
  → bài đăng: công thức do MÃ ghép từ bảng đã đối chiếu + mẹo
  → duyệt (thêm bảng định lượng + tóm tắt kế hoạch dựng) → render theo EditPlan (Botkeep / Kaggle chung assemble)
  → ảnh bìa theo mẫu kênh
```

**Thứ tự là cốt lõi:** kế hoạch dựng chốt TRƯỚC khi AI viết lời.
- Lời mới được viết cho khe thời gian của trục mới.
- Lời gốc của đoạn nào đi theo khúc hình chứa đoạn đó.
- Luật hiện tại vẫn đúng nguyên văn: "lời mới của mỗi đoạn chỉ nói thao tác của chính đoạn đó".

**Phạm vi:** chỉ đường remake chế độ `cook` và `review`. Nhạc, ảnh 📸, đoạn hay và video dài chia phần giữ nguyên, có thể mở rộng sau.

`REMAKE_STYLE=tắt` (mặc định) đi đúng đường cũ, không qua EditPlan. 25 snapshot hiện có phải giữ y nguyên.

## 3. Các khối mới

### 3.1 Hiểu món — `RecipeFacts` (thêm 1 lượt Gemini, có cache theo video)

**Đầu vào:**
- lời gốc theo đoạn (ngôn ngữ gốc);
- mô tả bài gốc (TikTok hay có công thức trong mô tả);
- tối đa 24 ảnh đã có sẵn từ bước phân tích (1 ảnh mỗi cảnh, 360px).

**Đầu ra** (JSON, có schema như các lời gọi hiện tại):
- `is_recipe`, `dish` (tên món), `servings` (chỉ khi nguồn nói)
- `ingredients[]`: `name_vi`, `name_src`, `qty`, `unit`, `first_seen` (số ảnh), và `evidence[]`. Mỗi bằng chứng có:
  - `type` ∈ speech / screen / desc
  - `seg` hoặc `img`
  - `quote`: trích nguyên văn
- `steps[]`: `id`, `kind`, `seg_from..seg_to` (các đoạn lời gốc), `ingredients[]`, `independent_of[]` (bước sơ chế không phụ thuộc nhau)
  - `kind` ∈ intro / ingredients / prep / cook / wait / plate / taste / reaction / promo / other
- `tips_src[]`: mẹo chính người gốc nói, kèm `seg`

**Không tin AI:** mọi trường ở trên chỉ là ĐỀ XUẤT, mã kiểm ở §3.2 và §3.5. AI lỗi hoặc quá hạn ngạch thì:
- bỏ lớp B;
- bài đăng không có công thức;
- ghi lý do vào ghi chú của job.

Video vẫn ra như lớp A.

### 3.2 Đối chiếu định lượng (khối mới quan trọng nhất)

**Kiểm bằng chứng của từng định lượng:**

- **`speech`:**
  - `quote` phải nằm trong lời gốc đoạn `seg`, so lỏng (bỏ dấu, gộp phụ âm hay nghe nhầm như `ingredients._loose`), độ giống ≥ 0,9;
  - VÀ con số + đơn vị đọc từ `quote` bằng bộ đọc số của MÃ phải bằng `qty unit`.
- **`desc`:** như trên, so với mô tả bài gốc.
- **`screen`** (hoãn sang S7: nguồn hiện tại không có chữ định lượng trên hình):
  - **Đọc chéo:** một lượt Gemini riêng gửi các ảnh được trích (gộp một lần), chỉ hỏi "chép nguyên văn chữ trên ảnh". Không nói trước số mong đợi, để AI không xác nhận theo.
  - Mã đọc số từ bản chép đó; phải trùng với số đã trích.

**Bộ đọc số** (S1 làm tiếng Việt; tiếng Trung / Anh hoãn sang S7; mở rộng `ingredients.numbers` / `_norm_num`, viết dạng bảng, có test từng dòng):
- **Tiếng Việt:** "nửa muỗng", "1/2 muỗng", "hai trăm gam", "1 lạng" = 100 g (miền Bắc), "nửa ký".
- **Tiếng Trung:**
  - Số: 一 二 两 三… 半; 克 = g; 千克 / 公斤 = kg.
  - **斤 = 500 g, 两 = 50 g. Bẫy:** "两勺" là "2 muỗng", nhưng "二两" là 100 g.
  - Đơn vị: 毫升 = ml; 汤匙 / 大勺 = muỗng canh; 茶匙 / 小勺 = muỗng cà phê; 碗 = chén; 杯 = ly; 个 / 颗 / 根 / 片 / 瓣; 分钟 = phút; 小时 = giờ; 度 = độ.
  - 适量 = "vừa đủ"; 少许 = "một chút".
- **Tiếng Anh:** g, grams, ml, minutes, °C / °F, cup, tbsp, tsp, oz, fl oz, lb, inch, pinch, "to taste".

**Đổi sang đơn vị chuẩn Việt Nam (đã chốt).** MÃ tính lại số, AI không tự đổi.

- **Chỉ dùng các đơn vị:** g, kg, ml, lít, muỗng canh, muỗng cà phê, °C, phút, giờ, cm, và đơn vị đếm (quả, củ, tép, nhánh, lát…).
- **Bảng đổi:**

| Gốc | Thành | Ví dụ |
|---|---|---|
| cup (chất lỏng) | × 240 ml | 2 cups sữa → 480 ml |
| cup (đồ khô) | gam theo bảng khối lượng riêng từng nguyên liệu: bột mì 125 g, đường cát 200 g, đường nâu 220 g, gạo 190 g, bột năng 130 g, bơ 225 g… | 1/2 cup đường → 100 g. Không có trong bảng thì đổi ra ml |
| tbsp / 汤匙 / 大勺 / 勺 | muỗng canh, GIỮ số (cùng ~15 ml) | 2 tbsp → 2 muỗng canh |
| tsp / 茶匙 / 小勺 | muỗng cà phê, giữ số | |
| oz (khối lượng) / fl oz (chất lỏng) | × 28,35 g / × 30 ml | 8 oz thịt → 230 g |
| lb | × 454 g | 1 lb → 450 g |
| 斤 / 两 (đơn vị) | × 500 g / × 50 g | 1 斤 thịt → 500 g; 二两 → 100 g; nhưng 两勺 = 2 muỗng canh |
| 公斤 / 千克, 克, 毫升, 升 | kg, g, ml, lít | |
| lạng, cân / ký (lời gốc tiếng Việt) | × 100 g, kg | 3 lạng → 300 g |
| °F | (F − 32) × 5/9, làm tròn 5 | 350°F → 175°C |
| inch | × 2,54 cm, làm tròn 0,5 | 1 inch → 2,5 cm |
| pinch / dash / 少许 | "1 chút" | không số |
| to taste / 适量 | "vừa đủ" | |

- **Làm tròn cho dễ đọc:**
  - < 10: giữ 1 chữ số lẻ khi cần (0,5);
  - 10–50: tới 1;
  - 50–200: tới 5;
  - ≥ 200: tới 10;
  - ≥ 1000 và chẵn thì ra kg / lít (1500 g → 1,5 kg).
  - Mã kiểm sai số làm tròn ≤ 5%.
- **Dùng số đã đổi ở mọi nơi:** lời đọc, thẻ trên video, bài đăng.
  - Đơn vị gốc chỉ hiện trong bảng đối chiếu lúc duyệt ("2 cups → 480 ml"), để bạn soát.
  - Lời đọc của nguồn nước ngoài được kiểm: số phải đúng số đã đổi.
  - Kiểm thêm giọng đọc đọc đúng "1,5 kg".
- **Cộng dồn ở phần Nguyên liệu:**
  - Cùng một nguyên liệu dùng ở nhiều bước (2 tép tỏi phi + 3 tép tỏi trộn sốt) → phần Nguyên liệu ghi tổng "5 tép tỏi"; phần Cách làm giữ số từng bước.
  - Chỉ cộng khi cùng đơn vị sau khi đổi. Khác đơn vị (1 muỗng canh + 50 g) thì ghi hai dòng.
  - Có phần ⚠️ / ❌ thì không cộng, ghi "vừa đủ".

**Trạng thái từng định lượng:**

| Trạng thái | Nghĩa | Được in lên video / bài đăng |
|---|---|---|
| ✅ lời gốc | khớp bằng chứng `speech` | có |
| ✅ mô tả | khớp `desc` | có |
| 📷 chữ trên hình | đọc chéo trùng | có |
| ⚠️ chỉ AI thấy | không có bằng chứng kiểm được | KHÔNG, ghi "vừa đủ" |
| ❌ mâu thuẫn | lời nói 200 g, chữ trên hình 250 g | KHÔNG in số. Lời đọc vẫn theo lời gốc như luật cũ. Báo bạn |
| ❔ số chưa gán | `numbers()` thấy trong lời gốc mà AI không gán cho nguyên liệu nào | không chặn, liệt kê khi duyệt |

**Dùng ở đâu:**
- **Lời đọc:**
  - Với nguồn KHÔNG phải tiếng Việt, kiểm thêm: số trong lời mới phải thuộc bảng đã đối chiếu, hoặc thuộc số của lời gốc.
  - Hiện `check_numbers` không bắt được trường hợp này.
- **Thẻ nguyên liệu trên video** (§3.5): chỉ dùng số đã ✅ hoặc 📷.
- **Công thức trong bài đăng** (§3.8): như trên.
- **Màn duyệt kịch bản** (Telegram / web): thêm bảng tóm tắt, ví dụ "Định lượng: 7/8 khớp lời gốc, 1 từ chữ trên hình, 0 mâu thuẫn".
- **Tự động đăng:**
  - có ❌ thì không đăng, đặt `job.no_autopost` (cơ chế đã có);
  - `QTY_CHECK=mềm` thì chỉ ghi chú.

### 3.3 Kế hoạch dựng — `EditPlan` (module mới `rmvideo/media/edl.py`)

**Một kiểu dữ liệu chung**, JSON hoá được để gửi Kaggle, chạy được trên Python 3.10:

```python
@dataclass(frozen=True)
class Piece:              # một khúc hình lấy từ video gốc
    a: float; b: float    # giây trên video gốc
    speed: float = 1.0    # 2–4 = tua nhanh đoạn chờ
    zoom: float = 1.0     # 1.0 / 1.12 / 1.18: đổi cỡ khung theo cảnh
    push: float = 0.0     # đẩy cận chậm trong khúc (cảnh đẹp): 0.06–0.08
    fx_in: str = ""       # "" | "flash" | "pulse": chuyển cảnh ngắn ở đầu khúc
    role: str = "main"    # main | teaser | montage | recap
    step: int | None = None
@dataclass(frozen=True)
class Overlay:            # chữ / thẻ trên trục thời gian MỚI
    t: float; dur: float; kind: str; text: str      # kind: ingredient | tip | skip | price
@dataclass(frozen=True)
class EditPlan:
    pieces: tuple[Piece, ...]; overlays: tuple[Overlay, ...] = (); stretch: float = 1.0
    def out_dur(self) -> float: ...
    def to_out(self, t_src: float) -> list[float]: ...      # một mốc gốc có thể hiện nhiều lần (montage)
    def map_segments(self, segs) -> list: ...              # đoạn lời gốc -> trục mới (đi theo khúc chứa nó)
    def map_windows(self, wins) -> list: ...               # cổng tiếng nền, cửa sổ giữ tiếng gốc
```

**Ràng buộc** (mã kiểm, test bằng nhiều bộ dữ liệu sinh ngẫu nhiên):
- **Không cắt giữa câu:** mép khúc chỉ nằm ở khoảng lặng ≥ 0,25 giây giữa hai đoạn lời gốc, hoặc ở điểm chuyển cảnh không có lời.
- **Khúc tua nhanh:** không có lời gốc, không có đoạn giữ tiếng gốc.
- **Khúc ngắn nhất:** 0,5 giây.
- **Độ dài:** trong ngân sách.
  - Nguồn ≤ 90 giây: ra ≤ 90 giây (Reels).
  - Nguồn 90–300 giây: ra ≤ 300 giây như hiện nay (đăng video thường).
  - Bản ngắn ≤ 90 giây cho Reels từ nguồn dài là phần riêng, chưa chốt (§10).
- **Gom gọn:**
  - đoạn mở đầu, cắt quảng cáo cuối và `stretch` đều thành trường hợp riêng của EditPlan;
  - **khi bật kiểu**, chỉ còn một đường dựng. Khi tắt, giữ đường cũ.

**Dựng bằng FFmpeg** (giữ RAM thấp trên Botkeep 2 GB):
- **Khúc theo thứ tự gốc:** chung một đầu vào; mỗi khúc `trim` + `setpts` (+ `setpts=PTS/speed`), rồi `concat`.
  - Thứ tự gốc nên khung hình không phải xếp hàng chờ, RAM không tăng.
- **Khúc lặp hoặc đảo** (montage, recap, cảnh dời về cuối):
  - KHÔNG kéo thêm nhiều đầu vào: mỗi decoder 1080p ăn khoảng 50–100 MB;
  - dựng trước một **clip phụ ngắn** (≤ 8 giây, mã hoá nhanh) bằng một lệnh `-ss` từng khúc;
  - bản dựng chính nhận clip này như một đầu vào (như đoạn mở đầu hiện nay);
  - tối đa 3 đầu vào phụ: mở đầu, montage, recap.
- **Tiếng nền** (đã tách giọng hoặc qua cổng) đi theo đúng các khúc:
  - `atrim` và `atempo` (nhiều tầng khi tốc độ > 2);
  - ở khúc tua nhanh hạ còn 50%.
- **Phụ đề, giọng đọc:** sinh trên trục mới, không phải map.
- **Lưu cùng job:**
  - lưu như `job.approved`, để bản đã duyệt dựng lại đúng y kế hoạch;
  - Kaggle nhận `spec["edit"]` và `assemble` dùng chung.

### 3.4 Lớp A — đổi nhịp, giữ thứ tự

| Kỹ thuật | Quy tắc | Chi phí dựng |
|---|---|---|
| Đổi cỡ khung theo cảnh | Mỗi cảnh luân phiên rộng (1.0) / cận (1.12), cứ 3–4 cảnh có một cận sâu (1.18). `crop` cố định rồi `scale`, không tính theo khung | gần 0 |
| Giấu cú cắt nhảy | Cắt bỏ thời gian chết giữa cùng một cảnh thì hai khúc hai bên PHẢI khác cỡ khung (kỹ thuật dựng phổ biến) | 0 |
| Đẩy cận chậm | 1–2 cảnh đẹp nhất ở 30% cuối (điểm ảnh có sẵn trong `cover.score_frames`): 1.0 → 1.08 | thấp (chỉ khúc đó tính theo khung) |
| Bỏ thời gian chết | Không lời gốc + ít chuyển động (so ảnh 2 khung/giây đã có) + không phải cảnh đẹp + không giữ tiếng gốc → còn ≤ 0,6 giây | giảm |
| Tua nhanh đoạn chờ | Không lời + cùng cảnh ≥ 4 giây + ít chuyển động, hoặc nhãn `wait`. Tốc độ 2–4 lần, đích ~1,5–2 giây | giảm |
| Chuyển cảnh ngắn | Chỉ ở ranh giới phần (mở đầu → nguyên liệu → các bước → thành phẩm). Chớp sáng 0,12 giây hoặc nhịp zoom, dùng lại `trendfx._windows` / `_decay` | thấp |
| Màu theo kênh | `BRAND_LOOK` (§3.9) thay công thức cố định. Mặc định `gốc` = công thức hiện tại | như cũ |
| Hạt phim nhẹ | `BRAND_GRAIN=1`: `noise` rất nhẹ trên kênh sáng. Bitrate thấp làm hạt thành khối, nên chỉ bật ở 1080p | +3–5% |
| Không lật gương | Video nấu ăn: không bao giờ (tay thuận, chữ bao bì) | — |

**Chữ ở đoạn tua nhanh** (đã chốt `TIME_SKIP_LABEL=auto`):
- **Hiện số thật:** chỉ khi lời gốc hoặc chữ trên hình có thời gian của đúng bước đó, ví dụ "hầm 30 phút" → thẻ "⏩ 30 PHÚT SAU". Số này cũng qua §3.2.
- **Không có số:** chỉ hiện biểu tượng ⏩, không bịa thời gian.
- Có thể chọn `icon` (luôn chỉ biểu tượng) hoặc `tắt`.

### 3.5 Lớp B — đảo thứ tự có kiểm soát

**Phép được làm:**

| Phép | Mô tả | Video nấu ăn | Video review |
|---|---|---|---|
| B1 Mở đầu bằng món | Đã có (đoạn mở đầu 3 giây), thành khúc đầu của EditPlan | ✓ | ✓ |
| B2 Điểm danh nguyên liệu | Sau câu hook: 4–6 khúc 0,7 giây LẶP LẠI khoảnh khắc từng nguyên liệu xuất hiện (`first_seen`). Thẻ "300g ba chỉ" chỉ dùng số đã đối chiếu. Các bước sau vẫn chiếu đủ, đúng thứ tự | ✓ | — |
| B3 Bỏ phần chào hỏi | Đoạn `intro` không có bước, nguyên liệu, số (`ingredients.find`/`numbers` rỗng) thì bỏ | ✓ | ✓ |
| B4 Dời cảm thán / khen về cuối | Chỉ `reaction` không chứa bước nào, đặt trước màn kết | ✓ | ✓ |
| B5 Tóm tắt trước khi kết | 3–4 giây, 4–6 bước chính, 0,6 giây mỗi khúc (lặp), lời tóm tắt cực ngắn | ✓ | — |
| B6 Đổi chỗ hai bước sơ chế | Tối đa 1 lần/video. Hai bước `prep` liền nhau, trước lần bắc chảo đầu, AI đánh dấu độc lập VÀ mã thấy không chung nguyên liệu | ✓ | — |
| B7 Thẻ giá / địa chỉ | Số đã đối chiếu (§3.2) | — | ✓ |
| B8 Kết luận về cuối | Đoạn chấm điểm / nhận xét cuối | — | ✓ |
| B9 Bỏ lời tán gẫu lúc chờ | Đoạn lời gốc AI dán nhãn `wait` / `other` VÀ mã thấy không có nguyên liệu, con số, động từ thao tác (`ingredients.find` / `numbers` rỗng) thì được bỏ lời. Khúc hình đó được tua nhanh như lớp A | ✓ | ✓ |

**Luật cứng** (mã kiểm, không tin AI). Vi phạm bất kỳ luật nào thì bỏ toàn bộ phép B, chỉ áp A, và ghi lý do:
- **R1 Đủ bước:** mỗi bước `prep` / `cook` / `plate` còn ≥ 60% thời lượng không phải thời gian chết, và ĐỦ mọi đoạn lời gốc của bước đó, trừ đoạn tán gẫu đã qua kiểm của B9.
- **R2 Đúng thứ tự:** lần xuất hiện chính (không tính montage, recap) của các bước `cook` / `plate` tăng dần đúng theo video gốc.
- **R3 Sơ chế trước nấu:** bước `prep` chỉ được hoán đổi với nhau, và phải xong trước bước `cook` đầu tiên dùng nguyên liệu của nó.
- **R4 Không mất số liệu:** mọi đoạn lời gốc có nguyên liệu hoặc con số đều còn, cùng thứ tự tương đối với nhau.
- **R5 Lặp không thay thế:** khúc montage / recap là bản lặp, không thay cho khúc chính.
- **R6 Giới hạn kỹ thuật:** đủ ngân sách độ dài, khúc ≥ 0,5 giây, ≤ 3 đầu vào phụ.
- **R7 Tua nhanh:** khúc tua nhanh không có lời gốc, không giữ tiếng gốc.

### 3.6 Lời đọc trên trục mới

- **Đoạn lời gốc:** map sang trục mới (`map_segments`). Khe thời gian và số từ tính lại theo độ dài mới. Lời nhắc `TIMED_PROMPT` giữ nguyên luật.
- **Đoạn mới không có lời gốc** (montage, recap, mẹo, câu chốt):
  - gửi AI dạng "đoạn k | 3,2 giây | 9–12 từ | (điểm danh nguyên liệu: 300g ba chỉ, 2 củ hành tím…)";
  - "lời gốc" giả này do MÃ sinh từ bảng đã đối chiếu, nên luật kiểm nguyên liệu và số đang có áp dụng luôn.
- **Câu chốt của kênh** (`BRAND_CLOSE`):
  - gắn vào đoạn cuối nếu còn chỗ;
  - phải qua `has_sub_cta`: vẫn KHÔNG kêu gọi đăng ký / theo dõi, theo yêu cầu cũ.
- **Câu hook:** vẫn nói về món ngay, không giới thiệu kênh. Thương hiệu nằm ở hình, màu, chữ, không chiếm 3 giây đầu.

### 3.7 Mẹo hay

**Nguồn mẹo:**
1. **Mẹo người gốc nói** (`tips_src`, có bằng chứng): ưu tiên.
2. **Mẹo kỹ thuật chung do AI viết:** tối đa 1. Luật:
   - gắn với một bước có trong video;
   - **không có con số**, trừ số đã đối chiếu;
   - không nói về sức khoẻ, chữa bệnh, giảm cân, bà bầu, trẻ nhỏ (danh sách cấm bằng regex);
   - không tên sản phẩm, thương hiệu;
   - không trái với cách làm trong video.

**Hiện ở đâu** (`TIPS`):
- `tắt`
- `bài đăng`: mặc định khi bật
- `bài đăng + video`: thẻ "💡 MẸO" 2,5 giây theo mẫu kênh, đặt ở bước liên quan; nói trong lời đọc nếu khe đó còn dư chỗ.

Mẹo AI viết hiện rõ trong màn duyệt. Tự động đăng chỉ dùng mẹo qua đủ luật.

### 3.8 Bài đăng có công thức

**Phần Facebook** (là phần tự động đăng gửi lên Fanpage, `autopost._post_text`) do MÃ ghép:

```
{câu hook}
{caption 1–2 câu}

🧾 NGUYÊN LIỆU (cho 2–3 người)        ← chỉ khi nguồn có nói khẩu phần
• 300g thịt ba chỉ
• 2 củ hành tím
• Nước mắm: vừa đủ                     ← số không đối chiếu được
👩‍🍳 CÁCH LÀM
1. Ướp thịt với … 15 phút.
2. …
💡 MẸO: …
#hashtag …
{dòng ghi nguồn nếu bật}
```

**Cách làm và cách kiểm:**
- **Câu từng bước:** AI viết trong CÙNG lượt viết lời (thêm trường `recipe_steps`, `tips`), không tốn thêm lượt gọi.
- **Mã kiểm:**
  - số trong các bước thuộc bảng đã đối chiếu;
  - nguyên liệu nhắc tới thuộc danh sách (`ingredients.find`);
  - số bước ≤ 8 và đúng thứ tự `steps`.
- **Sai:** viết lại 1 lần. Vẫn sai thì bỏ phần "Cách làm", giữ "Nguyên liệu".
- **TikTok:** caption + 1 dòng nguyên liệu gọn.
- **YouTube:** đủ công thức.
- **Video không phải món** (`is_recipe=false`) hoặc chế độ review: không có công thức. Review có "📍 địa chỉ, 💰 giá" đã đối chiếu.

### 3.9 Nhận diện kênh (bộ cố định theo kênh)

**Nhóm cài đặt mới "🎨 Nhận diện kênh".** Để trống hoặc `gốc` thì video ra y như bây giờ.

| Khoá | Ví dụ | Áp vào |
|---|---|---|
| `BRAND_NAME` (đã có) | Bếp Nhà Quế | màn kết, ảnh bìa |
| `BRAND_COLOR` | `#FF8A00` | chữ đang đọc của phụ đề karaoke (thay vàng cứng), vạch màu khung hook, thẻ nguyên liệu / mẹo / ⏩, ảnh bìa |
| `BRAND_LOOK` | `gốc` / `ấm mộc` / `tươi sáng` / `điện ảnh` | bộ màu `look_chain` (§3.4) |
| `BRAND_GRAIN` | 0/1 | hạt phim nhẹ |
| `BRAND_TAGLINE` | "Nấu nhanh, ăn là ghiền" | dòng dưới tên kênh ở màn kết |
| `BRAND_CLOSE` | "Bếp Quế chúc cả nhà ngon miệng nha" | câu chốt đọc cuối video |
| `BRAND_STING` | tệp âm thanh ≤ 1,5 giây, ≤ 150 KB | âm hiệu ở màn kết (không đặt đầu video để không lấn câu hook). Gửi sang Kaggle như logo (`logo_b64`) |
| `BRAND_SERIES` | "MÓN NGON 5 PHÚT" | nhãn loạt trên ảnh bìa + số tập tự tăng (tuỳ chọn) |
| `COVER_TEMPLATE` | `gốc` / `kênh` | ảnh bìa có vạch màu kênh, logo, tên loạt |

**Màn kết mới:** dừng hình ở cảnh món đẹp nhất, tên kênh + tagline + âm hiệu, 2,2 giây, không kêu gọi theo dõi.

**Phần cố định và phần thay đổi:** tông màu, phông, màu phụ đề, kiểu thẻ, màn kết, giọng đọc là phần nhận diện, cố định cho mọi video. Phần thay đổi là nhịp dựng (cỡ khung, khúc tua, montage), nên video không giống nhau mà vẫn nhận ra là của kênh.

### 3.10 Nhiều nguồn Facebook + TikTok

- **Chống trùng bằng dấu vân hình:**
  - **Vấn đề:** cùng một video thường bị nhiều trang đăng lại với chú thích khác nhau; cách so độ dài + chú thích hiện tại không bắt được.
  - **Cách làm:**
    - sau khi tải, lấy 8 ảnh ở vị trí tương đối cố định từ ảnh có sẵn, tính dHash 64 bit trên vùng giữa (chịu được cắt viền, logo góc), cả bản lật ngang;
    - trùng ≥ 6/8 ảnh (Hamming ≤ 10) thì dừng ngay, trước khi tốn Gemini / Kaggle, và đánh dấu đã dùng.
  - Lưu dấu vân trong `autopost.json` và cho video tự gửi tay (cảnh báo, không chặn).
- **Lọc nguồn khi tự động:**
  - chiều cao < 540px: phóng to xấu;
  - dải chữ in cứng che > 35% khung;
  - `is_recipe=false` khi chế độ là nấu ăn;
  - dài hơn mức cắt được về ≤ 90 giây mà vẫn giữ đủ bước.
- **Chế độ theo từng nguồn:** dòng nguồn ghi `link | review` thì nguồn đó làm chế độ review. Mặc định theo `AUTO_MODE`.
- **Nguồn nói tiếng Trung / Anh:** đối chiếu định lượng và đổi đơn vị theo §3.2; mẹo và công thức viết tiếng Việt.
- **Nhạc nền của video gốc:** giữ như hiện tại (đã chốt). Không làm phần phát hiện / thay nhạc.

## 4. Cài đặt mới

Tất cả vào `rmvideo/app/settings.py`: một chỗ khai báo, web tự hiện. Không khoá nào cần nhập lại mật khẩu. Tự gửi sang
Kaggle qua `snapshot` (không thuộc tiền tố bị loại).

| Khoá | Giá trị | Mặc định |
|---|---|---|
| `REMAKE_STYLE` | `tắt` / `nhẹ` (A) / `mạnh` (A+B) | `tắt` |
| `BRAND_COLOR`, `BRAND_LOOK`, `BRAND_GRAIN`, `BRAND_TAGLINE`, `BRAND_CLOSE`, `BRAND_STING`, `BRAND_SERIES`, `COVER_TEMPLATE` | §3.9 | trống / `gốc` / 0 |
| `TIPS` | `tắt` / `bài đăng` / `bài đăng + video` | `tắt` |
| `POST_RECIPE` | 0/1 | 0 |
| `QTY_CHECK` | `chặt` (không đối chiếu được thì không in số; mâu thuẫn thì không tự đăng) / `mềm` (in kèm "(ước lượng)") | `chặt` |
| `TIME_SKIP_LABEL` | `auto` / `icon` / `tắt` | `auto` |
| `AUTO_MIN_HEIGHT` | 540 | 540 |

Mặc định giữ hành vi cũ để snapshot không đổi. Bạn bật trong Cài đặt sau khi duyệt video mẫu.

Các khoá nhận diện kênh để trống được. Bạn điền trên web khi đã chốt kênh mới, không cần sửa mã.

## 5. Thay đổi theo module

| Module | Việc |
|---|---|
| `rmvideo/domain/recipe.py` (mới) | `RecipeFacts`, `Quantity`, đọc số đa ngôn ngữ, đổi đơn vị, kiểm bằng chứng. Hàm thuần, không I/O |
| `rmvideo/media/edl.py` (mới) | `EditPlan`, map mốc, kiểm ràng buộc, sinh chuỗi lọc từng khúc. Gửi Kaggle |
| `rmvideo/app/flow/style.py` (mới) | Các bước: hiểu món → đối chiếu → lập kế hoạch A/B → kiểm luật → lùi bậc. `Remake.run` gọi khi `REMAKE_STYLE≠tắt` |
| `prompt_engine.py` | Lời nhắc hiểu món + đọc chéo chữ; thêm `recipe_steps`, `tips` vào lượt viết lời; đoạn "lời gốc giả" cho montage |
| `ingredients.py` | Nối bộ đọc số mới; `check_numbers` so được với bảng đã đối chiếu |
| `rmvideo/media/filtergraph.py` | Chuỗi khúc (`trim` / `setpts` / `concat`), cỡ khung, đẩy cận, chuyển cảnh, bộ màu `BRAND_LOOK`, hạt phim |
| `rmvideo/media/renderer.py` | Thẻ nguyên liệu / mẹo / ⏩ / giá theo màu kênh, màn kết mới, âm hiệu, clip phụ cho montage |
| `subs.py`, `cover.py` | Màu kênh cho phụ đề; mẫu ảnh bìa kênh |
| `rmvideo/app/assemble.py`, `kaggle_worker.build_spec`, `kaggle_job.produce` | Nhận và truyền `edit`, `overlays`, `sting_b64`. Dùng chung nên Botkeep và Kaggle không lệch nhau |
| `rmvideo/domain/job.py`, `queue_manager` | Lưu `edit_plan`, `recipe` cùng job (dựng lại bản đã duyệt đúng y) |
| `review.py`, giao diện duyệt web / Telegram | Bảng định lượng, tóm tắt kế hoạch dựng, sửa được đoạn montage / mẹo |
| `autopost.py` | Dấu vân hình, lọc nguồn, chế độ theo nguồn, chặn đăng khi định lượng ❌ |
| Web cài đặt | Nhóm "🎨 Nhận diện kênh", tải âm hiệu (như tải logo) |

## 6. Kiểm thử

- **25 snapshot hiện có giữ y nguyên** khi `REMAKE_STYLE=tắt` và các khoá mới để mặc định.
- **Snapshot mới:**
  - kiểu `nhẹ`;
  - kiểu `mạnh` (RecipeFacts giả);
  - `mạnh` vi phạm R2 → lùi về A, có ghi chú;
  - AI hiểu món lỗi → lùi về A;
  - định lượng ❌ → không tự đăng;
  - bài đăng có công thức;
  - nguồn tiếng Trung.
- **Test đơn vị:**
  - bảng đọc số: mỗi dòng một ca, gồm cả bẫy 两;
  - đổi đơn vị;
  - kiểm bằng chứng;
  - `EditPlan.map_*` với dữ liệu sinh ngẫu nhiên (đơn điệu, phủ đủ, tổng độ dài);
  - từng luật R1–R7;
  - mẫu bài đăng (độ dài, ký tự đặc biệt).
- **Chuỗi lọc FFmpeg:**
  - chạy thật trên nguồn `lavfi` 20 giây (như test hiện có);
  - đo RAM tối đa khi dựng 60 giây 1080p có montage + recap.
  - **Ngưỡng:** ffmpeg ≤ 700 MB trên Botkeep.
- **Bundle Kaggle:** test tự đủ / Python 3.10 hiện có tự bắt module mới.
- **Bộ video vàng:** 6 video công thức + 1 video quảng cáo của Bảy Cua Quán (§1b). Mỗi giai đoạn xuất video mẫu để bạn duyệt trước khi sang giai đoạn sau.
  - Khi thêm nguồn nước ngoài thì bổ sung video tiếng Trung / có chữ định lượng (S7).

## 7. Chi phí chạy

| | Hiện tại | Thêm |
|---|---|---|
| Gemini | ~2–4 lượt/video | +1 (hiểu món) +1 nhỏ (đọc chéo, chỉ khi có số trên hình). Công thức và mẹo đi chung lượt viết lời |
| Dựng trên Botkeep | — | +10–20% (cỡ khung cố định gần như miễn phí; đẩy cận, chuyển cảnh, hạt phim tốn thêm). Bỏ thời gian chết làm video ngắn đi nên thường bù lại |
| Dựng trên Kaggle | — | không đáng kể |
| RAM | — | không tăng nhờ clip phụ (≤ 3 đầu vào phụ) |

## 8. Rủi ro và cách giảm

| Rủi ro | Cách giảm |
|---|---|
| AI dán nhãn bước sai, đảo sai | Luật cứng R1–R7 do mã kiểm. Sai là lùi về A. Phép đổi chỗ bước giới hạn 1 lần, chỉ trong khâu sơ chế |
| Lời lệch hình sau khi đảo | Kế hoạch dựng chốt TRƯỚC khi viết lời; lời gốc đi theo khúc của nó |
| Số sai trên thẻ / bài đăng | Chỉ in số có bằng chứng kiểm được; mâu thuẫn thì không in, không tự đăng |
| Mẹo AI sai / nhạy cảm | Mẹo người gốc ưu tiên; mẹo AI tối đa 1, có luật cấm, hiện rõ khi duyệt |
| Botkeep hết RAM | Clip phụ thay nhiều đầu vào; đo RAM trong test |
| Botkeep và Kaggle ra khác nhau | Chung `assemble` + `EditPlan` gửi kèm; test bundle |
| Nhạc gốc bị đối chiếu bản quyền | Đã chốt giữ như hiện tại. Theo dõi số video bị Facebook tắt tiếng / chặn; nhiều thì làm lại phần thay nhạc |
| Đổi đơn vị sai số | Bảng đổi + bảng khối lượng riêng do mã giữ, test từng dòng; sai số làm tròn ≤ 5%; bảng đối chiếu hiện đơn vị gốc khi duyệt |

**Lưu ý về nguồn:** đổi phong cách, thêm công thức, mẹo, lời bình giúp video có phần đóng góp riêng (Facebook ưu tiên kiểu nội dung này hơn đăng lại nguyên). Nhưng việc này không thay đổi quyền với video gốc. Nên bật `SOURCE_CREDIT`, và gỡ ngay khi chủ video yêu cầu. Kế hoạch này không có bước xoá logo hoặc watermark của người gốc.

## 9. Lộ trình (~12–15 ngày công, chưa tính S7)

| Giai đoạn | Nội dung | Công | Ra gì để bạn duyệt |
|---|---|---|---|
| S0 | Lưới an toàn: snapshot cho kiểu mới, bộ video vàng từ Bảy Cua Quán (tải, bóc lời, lưu cache) | 0,5 ngày | — |
| S1 | Hiểu món + đối chiếu định lượng (lời nói, mô tả; tiếng Việt) + quy đơn vị, cộng dồn + công thức và mẹo trong bài đăng + lọc video quảng cáo khi tự động + bổ sung cụm quảng cáo khóa học / quán (chưa đổi hình) | 2–2,5 ngày | bài đăng mẫu + bảng định lượng của 6 video vàng |
| S2 | Bộ nhận diện kênh (màu, bộ màu, hạt, phụ đề, khung hook, màn kết, ảnh bìa, âm hiệu, câu chốt) | 2–3 ngày | 2–3 video + ảnh bìa theo kênh |
| S3 | `EditPlan` + lớp A (dựng theo khúc, map lời, cỡ khung, bỏ thời gian chết, tua nhanh, ⏩, chuyển cảnh) | 3–4 ngày | video kiểu `nhẹ` |
| S4 | Lớp B (nhãn bước, điểm danh nguyên liệu, tóm tắt, dời cảm thán, đổi chỗ sơ chế, bỏ lời tán gẫu, luật R1–R7, lùi bậc) | 3–4 ngày | video kiểu `mạnh` |
| S5 | Khi thêm nguồn: dấu vân hình, lọc nguồn theo chất lượng, chế độ theo nguồn | 1 ngày | báo cáo trùng / lọc |
| S6 | Mẹo trên video (thẻ + lời), tinh chỉnh theo góp ý, tài liệu | 1–2 ngày | bản hoàn chỉnh |
| S7 (hoãn) | Nguồn nước ngoài / có chữ trên hình: đọc số tiếng Trung, Anh, đổi cup / oz / °F, đọc chéo chữ trên hình | 1–1,5 ngày | khi bắt đầu dùng nguồn đó |

**Lý do làm S1 trước:** không đụng phần dựng, rủi ro thấp, có ích ngay cho bài đăng. Bảng định lượng cũng là đầu vào của thẻ nguyên liệu ở S4.

## 10. Còn cần bạn

1. **Chốt kênh mới:** tên, màu, tông, tagline, câu chốt, âm hiệu, tên loạt (gợi ý ở §11).
   - Không chặn S1–S4: các khoá để trống, bạn điền trên web khi chốt.
2. **Bản ngắn cho Reels:** nguồn của bạn dài 2–5 phút nên video ra hiện cũng dài 2–5 phút, đăng dạng video thường.
   - Có muốn mỗi video nguồn ra THÊM một bản ≤ 90 giây cho Reels không? Bản này chọn các bước chính, thẻ nguyên liệu kèm số, công thức đủ trong bài đăng.
   - Làm được sau S4 vì dùng chung `EditPlan` và lớp B. Ước thêm 2–3 ngày.
3. **Lệnh bắt đầu code S1.**

## 11. Gợi ý định vị cho kênh mới

**Với kênh xây từ đầu, cái người xem nhớ trước tiên là LỜI HỨA của kênh, rồi mới đến màu và logo.**
- Chọn một lời hứa hẹp và lấy nguồn (`AUTO_SOURCES`, `AUTO_TIKTOK_SOURCES`) đúng chủ đề đó. Như vậy thuật toán và người xem đều hiểu kênh nhanh hơn.
- Ba hướng hợp với những gì bot làm tốt:

| | A. Bếp miền Tây thân tình | B. Công thức chuẩn, làm theo được (đề xuất) | C. Bếp điện ảnh / ASMR |
|---|---|---|---|
| Lời hứa | Món dân dã, kể như người nhà | Định lượng chính xác, xem xong nấu được ngay | Xem cho đã mắt, đã tai |
| Giọng văn | `mien_tay` (mặc định hiện tại) | `mien_nam` hoặc `pho_thong`, gọn, rõ | ít lời, nhiều đoạn giữ tiếng xèo (`OST_AUTO=1`) |
| Tông màu | `ấm mộc` | `tươi sáng` | `điện ảnh` |
| Màu kênh (ví dụ) | cam đất `#E07A2E` | xanh lá `#2BB673` | vàng đồng `#C9A227` |
| Điểm nhận diện | câu chốt thân tình cuối video | thẻ nguyên liệu kèm số đầu video + tóm tắt cuối + công thức đầy đủ trong bài | khung hình chậm, đẩy cận, chữ ít |
| Tên loạt (ví dụ) | "MÓN NGON BÀ CON" | "CÔNG THỨC CHUẨN #12" | "BẾP ĐÊM" |
| Nguồn hợp | kênh Việt nói nhiều | mọi nguồn, kể cả tiếng Trung / Anh (bot đã đổi đơn vị) | ASMR, Douyin |

**Vì sao đề xuất B:**
- Nó biến điểm mạnh riêng của bot thành lời hứa của kênh: đối chiếu định lượng, đổi đơn vị, công thức trong bài, điểm danh nguyên liệu, tóm tắt.
- Các trang đăng lại thông thường không có những thứ này.

**Với nguồn Bảy Cua Quán:**
- Đầu bếp quán miền Nam, nhiều công thức ghi "kinh doanh" (món bán được). Nếu muốn ngách hẹp hơn nữa, hướng B có thể thành **"công thức bán quán"**: định lượng chuẩn, mẹo giữ món ngon khi bán, chi phí nguyên liệu nếu nguồn có nói.
- Giọng văn `mien_nam` hợp vùng của nguồn (Biên Hòa) hơn `mien_tay` đang để mặc định.

**Lưu ý khi đặt tên:**
- Kiểm tên còn trống trên cả Facebook lẫn TikTok trước khi chốt.
- Không đặt gần giống tên kênh lớn đã có.
