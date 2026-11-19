"""Lời nhắc cho model chữ (llama.cpp trên Kaggle) và cách ghép prompt ảnh từ character bible — thuần chuỗi.

Model KHÔNG được đụng khối mô tả nhân vật và khối phong cách của prompt ảnh: model chỉ viết `action_en` (hành động trong
hình, 1-2 nhân vật); mã ghép [nhân vật nguyên văn từ bible] + [hành động] + [bối cảnh + thế giới] + [phong cách cố định].
Đó là cách rẻ nhất để 8 ảnh cùng một chất và nhân vật đỡ đổi dáng."""
from __future__ import annotations

from rmvideo.kids import story as story_mod

# Ngắn gọn: T5 của FLUX chỉ đọc 256 token; prompt 2 nhân vật + bối cảnh dài dễ bị cắt mất phần cuối (đo thật 18/11).
STYLE_EN = ("Pixar style 3D animation, cute children's storybook, soft rounded shapes, big expressive eyes, bright cheerful "
            "colors, soft warm light, vertical 9:16, no text")
# 19/11: nhân vật mỗi cảnh một kích cỡ (sóc to gấp đôi thỏ, thỏ mặt dài như người lớn) -> khoá tỉ lệ: tất cả cùng chiều cao,
# dáng bé con (đầu tròn to, má phúng phính, thân ngắn)
PROPORTION_EN = "All friends are toddler-sized and the same height: big round head, chubby cheeks, short body, standing on two legs"
# Hành động dự phòng theo vai của cảnh khi model không viết action_en bằng tiếng Anh (đo thật: viết tiếng Việt)
FALLBACK_ACTIONS_EN = ("looking around curiously", "playing together happily", "arguing, upset faces",
                       "sad and looking down", "a friend comforting the other, talking gently",
                       "helping each other, smiling", "hugging and making up, happy",
                       "all friends laughing and playing together")

SOUTH_VI = ("ba, má, trái banh, cái nón, cây dù, đôi vớ, chén, muỗng, dĩa, té, coi, mắc cỡ, tụi mình, mấy bạn, nha, hen, "
            "dễ thương, ngộ ghê")
NORTH_VI = "bố, quả bóng, cái mũ, cái ô, bít tất, bát, thìa, ngã, xem, xấu hổ, ạ, nhé, các cậu, tớ"


EXAMPLE_HOOK = ("Ôi, con diều cứ rớt hoài, Sóc Nhỏ phải làm sao đây?", "DIỀU RỚT HOÀI!")
EXAMPLE = [
    # truyện mẫu (viết tay theo sách tranh hay: câu ngắn có từ nối, tiếng động, điệp khúc có nhịp, thất bại hài, hỏi bé giữa
    # truyện, cảm xúc qua hành động, nét riêng nhân vật, kết vòng lại mở đầu; «nha» chỉ 2 lần); CHỈ để model học giọng kể
    # — bộ kiểm chặn khi chép lại ≥ 2 câu
    ("Hôm nay trời xanh ngắt, gió thổi mát rượi. Sóc Nhỏ ôm con diều màu đỏ chạy ra công viên, đuôi vẫy tít. «Hả? Gió to "
     "thế này thì diều bay cao phải biết!»",
     ["little_squirrel"], "Little Squirrel running on green grass holding a red kite, happy"),
    ("Thế là Sóc Nhỏ giơ diều lên thật cao. «Diều ơi, bay lên đi!» Sóc Nhỏ reo to rồi chạy một hơi.",
     ["little_squirrel"], "Little Squirrel holding a red kite up high, looking at the sky"),
    ("Vù vù! Con diều bay lên được một chút… rồi Bịch! Nó rớt ngay lên bụng Gấu Nâu đang ngủ trưa dưới gốc cây. Bé đoán xem "
     "Gấu Nâu có giật mình không?",
     ["little_squirrel", "brown_bear"], "A red kite landing on Brown Bear sleeping under a tree, Little Squirrel surprised"),
    ("Sóc Nhỏ rón rén nhặt diều, chạy nhanh hơn nữa. «Chạy, chạy, chạy nào!» Bịch! Lần này diều rớt vào bụi hoa, dính đầy "
     "cánh hoa vàng.",
     ["little_squirrel"], "Little Squirrel running fast pulling a kite string, a kite stuck in yellow flowers"),
    ("Sóc Nhỏ leo lên mô đất cao mà thả. «Chạy, chạy, chạy nào!» Diều lên, rồi lại xuống. Sóc Nhỏ ngồi bệt, đuôi rũ xuống đất.",
     ["little_squirrel"], "Little Squirrel sitting on the grass, sad, a red kite beside"),
    ("Gấu Nâu dụi mắt đi tới: «Ừm… để mình cầm diều cho, bạn cứ chạy.» Sóc Nhỏ gật đầu: «Chạy, chạy, chạy nào!» Vù… Con "
     "diều bay lên cao, cao thật là cao!",
     ["brown_bear", "little_squirrel"], "Brown Bear holding a red kite high, Little Squirrel running with the string"),
    ("Gấu Nâu cười hề hề: «Thấy chưa, chưa được thì mình đổi cách rồi thử tiếp, đừng bỏ cuộc nha.» Sóc Nhỏ gật đầu lia lịa, "
     "đuôi lại vẫy tít.",
     ["brown_bear", "little_squirrel"], "Brown Bear smiling and talking to Little Squirrel"),
    ("Hai bạn ngước nhìn con diều đỏ bay giữa trời xanh ngắt, gió vẫn thổi mát rượi. Sóc Nhỏ reo: «Hả? Bay cao thiệt rồi!» "
     "Gấu Nâu cười hề hề.",
     ["little_squirrel", "brown_bear"], "Little Squirrel and Brown Bear looking up at a red kite flying in the blue sky"),
]


def example_sentences() -> list[str]:
    """Các câu ≥ 5 chữ của truyện mẫu (để bộ kiểm bắt chép mẫu)."""
    import re
    out = []
    for text, _, _ in EXAMPLE:
        for sent in re.split(r"[.!?…]+", text):
            sent = sent.strip(" «»\"")
            if len(sent.split()) >= 5 and sent not in out:        # điệp khúc lặp 3 lần chỉ tính một
                out.append(sent)
    return out


def _example_block() -> str:
    return (f"  hook: {EXAMPLE_HOOK[0]} · hook_text: {EXAMPLE_HOOK[1]}\n"
            + "\n".join(f"  Cảnh {i}: {t}\n    characters: {c} · action_en: {a}" for i, (t, c, a) in enumerate(EXAMPLE, 1)))


def system_prompt(channel: dict) -> str:
    return (f"Bạn là người kể chuyện của kênh «{channel.get('name', 'Kể Bé Nghe')}» cho bé 3-6 tuổi, giọng miền Nam Việt "
            "Nam: ấm, vui, chậm rãi, kể như cô giáo mầm non kể cho cả lớp nghe. Lời kể TRÔI CHẢY như kể miệng: câu 5-14 chữ, "
            "nối nhau bằng từ nối (thế là, rồi, nhưng mà, bỗng, lúc đó), không viết cụt lủn kiểu 2-3 chữ một câu; từ quen thuộc "
            "với bé, có tiếng động, có câu điệp khúc lặp lại, nhiều lời thoại. Lời thoại LUÔN đặt trong «…» và có tên người nói "
            "ngay trước ngoặc (Gấu Nâu nói: «…», Sóc Nhỏ reo: «…») — mỗi nhân vật được đọc bằng giọng riêng. Nhân vật xưng "
            f"«mình», gọi nhau «bạn» hoặc tên. Dùng từ miền Nam: {SOUTH_VI}. KHÔNG dùng từ miền Bắc: {NORTH_VI}. "
            "Không bạo lực, không đáng sợ, không ai bị phạt; người lớn (nếu có) luôn dịu dàng. "
            "Viết THUẦN tiếng Việt, không một chữ tiếng Anh. Chỉ trả lời bằng JSON đúng schema, không giải thích thêm.")


def user_prompt(brief: dict, data: dict, feedback: list[str] | None = None, previous: dict | None = None) -> str:
    chars = data["characters"]
    main = chars[brief["main"]]
    lesson = story_mod.by_id(data["lessons"], brief["lesson"])
    setting = story_mod.by_id(data["settings"], brief["setting"])
    conflict = story_mod.by_id(data["conflicts"], brief["conflict"])
    cast = "; ".join(f"{c['name_vi']} ({c['personality_vi']}; nét riêng: {c.get('quirk_vi', '')})" for c in chars.values())
    must = ", ".join(f"«{w['vi']}»" for w in story_mod.must_mention(brief, data))   # KHÔNG đưa tiếng Anh cho model
    roles = "\n".join(f"  Cảnh {i}: {r}" for i, r in enumerate(story_mod.plot_beats(brief, data), 1))
    plot = story_mod.by_id(data.get("plots") or [], brief.get("plot") or "")
    keys = ", ".join(chars)
    names_en = ", ".join(c["name_en"] for c in chars.values())
    text = (f"Viết một truyện kể cho bé 3-6 tuổi, xảy ra ở {data['world']['name']}, nơi bốn bạn nhỏ sống cùng nhau: {cast}.\n"
            f"Nhân vật chính hôm nay: {main['name_vi']}. Bối cảnh: {setting['vi']}. Chuyện xảy ra: {main['name_vi']} "
            f"{conflict['vi']}. Bài học: {lesson['vi']}.\n")
    if must:
        text += (f"Đồ vật / con vật / việc có thể xuất hiện ở đây: {must}. Lồng ÍT NHẤT 3 thứ trong số đó vào truyện một cách tự "
                 "nhiên (là đồ bạn ấy cầm, thấy, dùng trong chuyện), KHÔNG ép thêm câu chỉ để nhắc tới; thứ nào không hợp thì bỏ.\n")
    if plot:
        refrain = (plot.get("refrain") or "").replace("{main}", main["name_vi"])
        twist = (plot.get("twist") or "").replace("{main}", main["name_vi"])
        text += (f"Khung truyện: {plot['vi']} (kiểu {plot.get('source', '')}). Các nhịp dưới đây là Ý, KHÔNG chép chữ của nhịp: "
                 "mỗi cảnh kể bằng chi tiết riêng của truyện hôm nay (đồ vật, màu sắc, tiếng động, lời thoại của từng bạn); vật / "
                 "việc nêu ở câu móc phải xuất hiện lại ở cảnh 2, 3, 6 và 8 (sợi chỉ xuyên suốt).\n"
                 + (f"Điệp khúc gợi ý cho khung này: «{refrain}» (tiếng lòng / lời động viên hợp chuyện; có thể đổi chữ cho hợp, "
                    "nhưng điệp khúc phải có nghĩa đúng với tình huống)"
                    + (f"; ở cảnh 6 khi chuyện xoay chiều, đảo thành «{twist}» (hay câu tương tự)" if twist else "") + ".\n"
                    if refrain else ""))
    text += (f"\nĐúng {story_mod.N_SCENES} cảnh, theo thứ tự:\n{roles}\n"
            "Cách kể:\n"
            f"- Mỗi cảnh 2-4 câu, {story_mod.SCENE_MIN + 6}-{story_mod.SCENE_MAX - 6} chữ; cả truyện "
            f"{story_mod.WORDS_MIN + 10}-{story_mod.WORDS_MAX - 15} chữ (video chỉ 60-88 giây, dài hơn là bị cắt). Một chuyện nhỏ kể "
            "liền mạch từ đầu tới cuối; câu đầu mỗi "
            "cảnh nối với cảnh trước (thế là / lúc đó / nhưng rồi).\n"
            "- Có tiếng động (Bịch! Vù vù! Cốc cốc! Ối!). Có MỘT câu điệp khúc ngắn (dưới 8 chữ) lặp 2-3 lần ở cảnh 4, 5 (và 6) "
            "— chỉ đặt vào chỗ ĐÚNG NGHĨA với tình huống; ở cảnh 6 khi chuyện xoay chiều, có thể ĐẢO điệp khúc cho vui (theo gợi ý "
            "ở trên, đúng với chuyện hôm nay). NGOÀI điệp khúc, không câu nào, cụm nào lặp lại.\n"
            "- Mỗi cảnh chỉ có 1-2 bạn trong hình: lời kể gọi TÊN bạn có mặt, KHÔNG «các bạn / cả nhóm / mấy bạn» khi chỉ có "
            "một bạn khác; mỗi bạn được nhắc trong lời phải có trong `characters` của cảnh.\n"
            f"- Tiểu từ «nha», «hen» ở cuối câu: cả truyện tối đa {story_mod.TAIL_MAX} lần (nhiều quá nghe ngang); câu móc "
            "không kết bằng «nha». Câu kết bình thường bằng dấu chấm / chấm than.\n"
            "- LIỀN MẠCH: cảnh sau xảy ra VÌ cảnh trước (nhân quả), ba lần thử khác nhau và leo thang, lần ba thành công nhờ "
            "bạn giúp hoặc nghĩ ra cách khác; bài học rút ra từ chính chuyện đó, không giảng chung chung.\n"
            "- Cảnh 7: bạn kia nói bài học bằng MỘT câu ngắn như bạn bè nói với nhau, có ít nhất một chữ trong: "
            + ", ".join(lesson.get("keywords", [])) + " (đừng gom hết các chữ vào một câu, đừng giảng)"
            + ". Cảnh 8 kết vui bằng HÌNH ẢNH vòng lại cảnh 1 (các bạn chơi / cười / cùng ngắm), câu cuối ngắn và ấm; KHÔNG "
            "«các bé ơi», không dặn dò lần nữa (bài học đã nói ở cảnh 7, nói thêm nghe giảng).\n"
            f"- `hook`: câu nói ĐẦU video (3 giây đầu quyết định bé xem tiếp hay lướt): MỘT câu hỏi hoặc câu cảm "
            f"{story_mod.HOOK_WORDS[0]}-{story_mod.HOOK_WORDS[1]} chữ, gợi ngay chuyện sắp xảy ra và nhân vật chính, không "
            "kể trước cách giải quyết. `hook_text`: 2-5 chữ IN HOA tóm cái móc đó, hiện to trên màn hình.\n"
            "- Viết THUẦN tiếng Việt, không chữ tiếng Anh, không giải nghĩa từ (phần dạy tiếng Anh mã sẽ tự thêm).\n"
            "BÍ KÍP KỂ CHO CUỐN (học từ sách tranh hay như The Gruffalo, We're Going on a Bear Hunt, Chú Dê Đen):\n"
            "- HỎI BÉ giữa truyện 1-2 lần (cảnh 2-6), ngắn, có chữ «bé», đặt ở CUỐI cảnh như lật trang sách: «Bé đoán xem Thỏ "
            "Trắng giấu ở đâu?» — rồi cảnh sau mở đầu bằng câu trả lời («Hoá ra giấu sau gốc cây!»).\n"
            "- Tiêu đề phải nói đúng đồ vật / chuyện CÓ trong truyện (truyện về trái táo thì không đặt «chiếc bánh»).\n"
            "- Điệp khúc có NHỊP, hát theo được (3-6 chữ, lặp âm): «Chạy, chạy, chạy nào!», «Tìm nữa, tìm nữa nào!».\n"
            "- Thất bại phải HÀI: chi tiết ngộ nghĩnh + tiếng động (diều rớt lên bụng Gấu Nâu đang ngủ, bánh dính lên mũi).\n"
            "- Cảm xúc qua HÀNH ĐỘNG, không gọi tên: «tai thỏ cụp xuống», «đuôi rũ xuống đất» thay cho «Thỏ buồn».\n"
            "- Cảnh 1 có chi tiết giác quan (màu, mùi, âm thanh). Dùng NÉT RIÊNG của bạn chính ít nhất 2 lần (câu cửa miệng, "
            "cử chỉ quen).\n"
            "- Lần thử thứ ba có chút BẤT NGỜ (cách giải quyết đến từ chỗ không ngờ, hay từ bạn nhỏ nhất).\n"
            "- KẾT VÒNG LẠI: câu cuối gọi lại hình ảnh hay câu của cảnh 1, nghe ấm và trọn.\n"
            "- Mỗi cảnh ghi `characters`: 1-2 nhân vật CÓ MẶT trong hình (khoá: " + keys + "), và `action_en`: MỘT câu "
            f"TIẾNG ANH (English only), tối đa 25 từ, gọi nhân vật bằng tên tiếng Anh ({names_en}), tả hành động + cảm xúc + "
            "đồ vật trong hình, thì hiện tại; không tả ngoại hình hay quần áo (đã có sẵn), không chữ.\n"
            f"- `title`: tiêu đề tiếng Việt 20-{story_mod.TITLE_MAX} ký tự, có tên {main['name_vi']}, đặt theo CHUYỆN XẢY RA để "
            "gây tò mò (ví dụ «Thỏ Trắng và chiếc bình hoa vỡ»), KHÔNG lộ bài học hay kết («biết nhận lỗi», «học được…»), không "
            "chữ tiếng Anh.\n"
            "- `description`: 2-3 câu tiếng Việt cho ba mẹ: truyện dạy bé điều gì.\n"
            "- `hashtags`: 2 hashtag tiếng Việt không dấu theo bài học (ví dụ: #giuloihua, #bengoan).\n\n"
            "Truyện MẪU để học cách kể (chỉ học giọng kể và nhịp; KHÔNG chép lại câu nào, truyện hôm nay khác nhân vật, bối "
            "cảnh, chuyện xảy ra):\n" + _example_block() + "\n")
    if feedback and previous:
        draft = (f"Tiêu đề: {previous.get('title', '')}\nCâu móc: {previous.get('hook', '')} | Chữ to: {previous.get('hook_text', '')}\n"
                 + "\n".join(f"Cảnh {i}: {sc.get('text', '')}" for i, sc in enumerate(previous.get("scenes") or [], 1)))
        text += ("\nBẢN NHÁP TRƯỚC (giữ cốt truyện, lời thoại hay, điệp khúc, câu hỏi bé, nét riêng của bản này):\n" + draft
                 + "\nBản nháp chưa đạt vì:\n" + "\n".join(f"- {p}" for p in feedback)
                 + "\nSỬA đúng các lỗi trên, đổi càng ít càng tốt (dài quá thì cắt câu tả cảnh / câu đệm, không cắt lời thoại hay "
                 "điệp khúc), rồi trả lại TOÀN BỘ JSON đủ 8 cảnh với characters và action_en của từng cảnh.")
    elif feedback:
        text += ("\nBản trước chưa đạt vì:\n" + "\n".join(f"- {p}" for p in feedback)
                 + "\nViết lại TOÀN BỘ truyện, sửa đúng các lỗi trên và giữ ý hay. Kiểm lại trước khi trả: đủ 8 cảnh"
                 + (", có ít nhất 3 thứ trong: " + must if must else "")
                 + ", không chữ tiếng Anh trong lời, action_en bằng tiếng Anh, có hook và hook_text.")
    return text


REVISE_CHECK = (
    "1. Câu `hook` có khiến bé tò mò ngay giây đầu không (hỏi/cảm, nhắc nhân vật chính và chuyện sắp xảy ra, KHÔNG lộ cách giải quyết)?\n"
    "2. Mỗi cảnh có xảy ra VÌ cảnh trước không (nhân quả rõ, không nhảy cóc, không lặp ý)?\n"
    "3. Ba lần thử (cảnh 4, 5, 6) có KHÁC nhau, leo thang và lần ba thành công nhờ bạn giúp / cách khác không?\n"
    "4. Bài học (cảnh 7) có rút ra từ chính chuyện vừa xảy ra không (không giảng chung chung)?\n"
    "5. Câu ngắn (dưới 10 chữ), có tiếng động, điệp khúc đặt đúng chỗ có nghĩa (không dán máy móc), lời thoại tự nhiên như trẻ "
    "con, giọng miền Nam? Lời kể có gọi «các bạn / cả nhóm» khi cảnh chỉ có 2 bạn không?\n"
    "6. Có câu nào khó hiểu với bé 3-6 tuổi, chữ thừa, lỗi chính tả, hay nhân vật đổi tên không?\n"
    "7. «nha/hen» cuối câu có quá 3 lần không? Có câu/cụm nào lặp lại ngoài điệp khúc không? Câu móc có kết bằng «nha» không?\n"
    "8. Có câu nào lạc đề, chỉ để nhắc tới một đồ vật cho có (như «ngoài đường xe hơi chạy ngang») không? Bỏ hẳn câu đó.\n"
    "9. Tiêu đề có lộ bài học / kết không? Phải gợi tò mò theo chuyện xảy ra.\n"
    "10. Đọc to lên có trôi chảy không? Câu cụt lủn thì nối lại bằng từ nối; lời thoại đã trong «…» và có tên người nói ngay "
    "trước ngoặc chưa?\n"
    "11. CUỐN chưa? Có câu hỏi cho bé giữa truyện (cảnh 2-6)? Thất bại có chi tiết hài + tiếng động? Cảm xúc tả qua hành động "
    "(tai cụp, đuôi rũ) thay vì gọi tên — một cảnh không gọi tên cảm xúc hai lần («buồn thiu… buồn rầu»)? Bạn chính có dùng nét "
    "riêng / câu cửa miệng? Kết có vòng lại hình ảnh cảnh 1?\n"
    "Không chỉ sửa lỗi: hãy NÂNG bản nháp lên mức một cuốn sách tranh hay — thêm chi tiết ngộ nghĩnh, bớt câu nhạt, giữ cốt truyện.")


def revise_messages(story: dict, brief: dict, data: dict, channel: dict) -> list[dict[str, str]]:
    """Lượt tự soát: bản nháp + checklist -> bản sửa cùng schema (giữ đề, giữ các đồ vật phải nhắc tới)."""
    import json
    must = ", ".join(f"«{w['vi']}»" for w in story_mod.must_mention(brief, data))
    draft = {k: story.get(k) for k in ("title", "hook", "hook_text", "scenes", "description", "hashtags")}
    text = ("Đây là bản nháp truyện (JSON):\n" + json.dumps(draft, ensure_ascii=False, indent=1)
            + "\n\nSoát theo checklist, chỗ nào chưa đạt thì SỬA, chỗ đã tốt thì giữ nguyên:\n" + REVISE_CHECK
            + "\n\nGiữ đúng: 8 cảnh, mỗi cảnh 2-4 câu ngắn, thuần tiếng Việt (không chữ tiếng Anh), vẫn có ít nhất 3 thứ trong: "
            + (must + ", " if must else "") + "action_en bằng tiếng Anh, cùng nhân vật chính. Trả JSON đầy đủ cùng schema.")
    return [{"role": "system", "content": system_prompt(channel)}, {"role": "user", "content": text}]


def messages(brief: dict, data: dict, channel: dict, feedback: list[str] | None = None,
             previous: dict | None = None) -> list[dict[str, str]]:
    """previous: bản nháp trước (đã chuẩn hoá) khi viết lại theo lỗi -> model SỬA bản đó thay vì viết truyện khác (19/11:
    Hydrogen nháp hay nhưng dài, flash-lite viết lại từ đầu thì mất cái hay và vẫn dài)."""
    return [{"role": "system", "content": system_prompt(channel)},
            {"role": "user", "content": user_prompt(brief, data, feedback, previous)}]


def schema(data: dict) -> dict:
    """JSON schema cho grammar của llama.cpp: model không thể trả JSON hỏng hay thiếu trường."""
    keys = list(data["characters"])
    scene = {"type": "object",
             # minLength ép độ dài từng cảnh ngay trong grammar (đo thật: model viết 9-11 chữ/cảnh khi chỉ nhắc bằng lời)
             "properties": {"text": {"type": "string", "minLength": 60, "maxLength": 230},
                            "characters": {"type": "array", "items": {"type": "string", "enum": keys},
                                           "minItems": 1, "maxItems": 2},
                            "action_en": {"type": "string", "minLength": 10, "maxLength": 300}},
             "required": ["text", "characters", "action_en"], "additionalProperties": False}
    return {"type": "object",
            "properties": {"title": {"type": "string", "minLength": 6, "maxLength": story_mod.TITLE_MAX + 10},
                           "hook": {"type": "string", "minLength": 10, "maxLength": 110},
                           "hook_text": {"type": "string", "minLength": 4, "maxLength": 32},
                           "scenes": {"type": "array", "items": scene, "minItems": story_mod.N_SCENES,
                                      "maxItems": story_mod.N_SCENES},
                           "description": {"type": "string", "maxLength": 1200},
                           "hashtags": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 4}},
            "required": ["title", "hook", "hook_text", "scenes", "description", "hashtags"], "additionalProperties": False}


def shirt_color(char: dict) -> str:
    """Màu áo trong prompt_en của nhân vật («plain red t-shirt» -> «red»); không có thì rỗng."""
    import re
    m = re.search(r"\b(red|green|blue|yellow)\b[^.,]*t-shirt", char.get("prompt_en") or "")
    return m.group(1) if m else ""


def image_prompt(scene: dict, brief: dict, data: dict, idx: int = 0, use_lora: bool = False) -> str:
    """[hành động model viết (tiếng Anh; không phải tiếng Anh thì dùng hành động dự phòng theo vai cảnh idx)] + [mô tả nhân
    vật nguyên văn, áo màu gì nói trước] + [bối cảnh + thế giới] + [phong cách cố định]. Giữ ngắn cho vừa 256 token T5."""
    chars = data["characters"]
    keys = [k for k in scene.get("characters", []) if k in chars][:2] or [brief["main"]]
    action = (scene.get("action_en") or "").strip().rstrip(".")
    if not story_mod.is_english(action) or len(action.split()) < 3:
        names = " and ".join(chars[k]["name_en"] for k in keys)
        action = f"{names} {FALLBACK_ACTIONS_EN[idx % len(FALLBACK_ACTIONS_EN)]}"
    descs = ". ".join(((chars[k].get("lora_token", "") + " ") if use_lora and chars[k].get("lora_token") else "")
                      + chars[k]["prompt_en"] for k in keys)       # use_lora: từ khoá LoRA («thotrang») đứng trước mô tả
    setting = story_mod.by_id(data["settings"], brief["setting"]).get("en", "")
    # «exactly»: đo thật 18/11, FLUX hay vẽ thêm bản sao nhân vật (hai thỏ, hai sóc) hoặc một con gấu con lạ
    who = "Exactly two friends in the picture" if len(keys) == 2 else "Exactly one character in the picture"
    return (f"{who}: {action}. {descs}. {PROPORTION_EN}. Setting: {setting}, {data['world']['prompt_en']}. {STYLE_EN}, "
            "no other animals")


def post_pack(story: dict, brief: dict, channel: dict, data: dict) -> tuple[str, str, str]:
    """(tiêu đề, mô tả, hashtag) cho bài đăng: mô tả model viết + khối từ vựng + bài học + tagline; hashtag cố định của
    kênh + hashtag theo bài học model gợi ý."""
    lesson = story_mod.by_id(data["lessons"], brief["lesson"])
    words = "\n".join(f"🔤 {w['en']} = {w['vi']}" for w in brief["words"])
    desc = (f"{story.get('description', '').strip()}\n\n{words}\n\n📖 Bài học: {lesson.get('vi', '')}."
            + (f"\n{channel['tagline']}" if channel.get("tagline") else ""))
    import re
    fixed = [t for t in re.split(r"[,\s]+", str(channel.get("hashtags") or "")) if t.startswith("#")]
    tags = fixed + [t for t in story.get("hashtags", []) if t not in fixed]
    return story.get("title", ""), desc.strip(), " ".join(tags)
