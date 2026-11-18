"""Lời nhắc cho model chữ (llama.cpp trên Kaggle) và cách ghép prompt ảnh từ character bible — thuần chuỗi.

Model KHÔNG được đụng khối mô tả nhân vật và khối phong cách của prompt ảnh: model chỉ viết `action_en` (hành động trong
hình, 1-2 nhân vật); mã ghép [nhân vật nguyên văn từ bible] + [hành động] + [bối cảnh + thế giới] + [phong cách cố định].
Đó là cách rẻ nhất để 8 ảnh cùng một chất và nhân vật đỡ đổi dáng."""
from __future__ import annotations

from rmvideo.kids import story as story_mod

# Ngắn gọn: T5 của FLUX chỉ đọc 256 token; prompt 2 nhân vật + bối cảnh dài dễ bị cắt mất phần cuối (đo thật 18/11).
STYLE_EN = ("Pixar style 3D animation, cute children's storybook, soft rounded shapes, big expressive eyes, bright cheerful "
            "colors, soft warm light, vertical 9:16, no text")
# Hành động dự phòng theo vai của cảnh khi model không viết action_en bằng tiếng Anh (đo thật: viết tiếng Việt)
FALLBACK_ACTIONS_EN = ("looking around curiously", "playing together happily", "arguing, upset faces",
                       "sad and looking down", "a friend comforting the other, talking gently",
                       "helping each other, smiling", "hugging and making up, happy",
                       "all friends laughing and playing together")

SOUTH_VI = ("ba, má, trái banh, cái nón, cây dù, đôi vớ, chén, muỗng, dĩa, té, coi, mắc cỡ, tụi mình, mấy bạn, nha, hen, "
            "dễ thương, ngộ ghê")
NORTH_VI = "bố, quả bóng, cái mũ, cái ô, bít tất, bát, thìa, ngã, xem, xấu hổ, ạ, nhé, các cậu, tớ"


def system_prompt(channel: dict) -> str:
    return (f"Bạn là người kể chuyện cho trẻ em 3-8 tuổi của kênh «{channel.get('name', 'Kể Bé Nghe')}», giọng miền Nam "
            "Việt Nam: ấm, vui, dễ thương, kể như ba má kể con nghe trước khi ngủ. Câu ngắn (mỗi câu dưới 12 chữ), từ dễ "
            f"hiểu, nhiều lời thoại của nhân vật. Dùng từ miền Nam: {SOUTH_VI}. KHÔNG dùng từ miền Bắc: {NORTH_VI}. "
            "Không bạo lực, không đáng sợ, không ai bị phạt nặng; người lớn (nếu có) luôn dịu dàng. "
            "Chỉ trả lời bằng JSON đúng schema, không giải thích thêm.")


def user_prompt(brief: dict, data: dict, feedback: list[str] | None = None) -> str:
    chars = data["characters"]
    main = chars[brief["main"]]
    lesson = story_mod.by_id(data["lessons"], brief["lesson"])
    setting = story_mod.by_id(data["settings"], brief["setting"])
    conflict = story_mod.by_id(data["conflicts"], brief["conflict"])
    cast = "; ".join(f"{c['name_vi']} ({c['personality_vi']})" for c in chars.values())
    words = "; ".join(f"{w['en']} = {w['vi']}" for w in brief["words"])
    word_lines = "\n".join(f"  - «{w['en']}» ({w['vi']}), câu mẫu: «{story_mod.word_line(w, data)}»" for w in brief["words"])
    names_en = ", ".join(c["name_en"] for c in chars.values())
    roles = "\n".join(f"  Cảnh {i}: {r}" for i, r in enumerate(story_mod.SCENE_ROLES, 1))
    keys = ", ".join(chars)
    text = (f"Viết một truyện ngắn xảy ra ở {data['world']['name']}, nơi bốn bạn nhỏ sống cùng nhau: {cast}.\n"
            f"Nhân vật chính hôm nay: {main['name_vi']}.\n"
            f"Bối cảnh: {setting['vi']}. Chuyện xảy ra: {main['name_vi']} {conflict['vi']}.\n"
            f"Bài học của truyện: {lesson['vi']}.\n"
            f"Từ tiếng Anh bé học hôm nay: {words}.\n\n"
            f"Cấu trúc đúng {story_mod.N_SCENES} cảnh, theo thứ tự:\n{roles}\n"
            f"Yêu cầu:\n"
            f"- Mỗi cảnh 2-4 câu, 18-30 chữ; cả truyện 170-220 chữ. Kể có chuyện xảy ra thật (ai làm gì, rồi sao), "
            "bằng hành động và lời thoại; KHÔNG hỏi dồn dập, KHÔNG liệt kê.\n"
            "- Cả bốn bạn đều phải được nhắc tên (gọi đúng tên: "
            + ", ".join(c["name_vi"] for c in chars.values()) + ").\n"
            "- Từ tiếng Anh: viết đúng chính tả tiếng Anh, chữ thường, nằm trong lời thoại; MỖI từ dưới đây đều phải có "
            "mặt, không được bỏ sót từ nào:\n" + word_lines + "\n"
            "  Mỗi từ nằm trong MỘT CÂU CÓ NGHĨA của nhân vật, tiếng Việt chen từ tiếng Anh đúng chỗ như câu mẫu (dùng y "
            "câu mẫu hoặc sửa cho hợp truyện, nhân vật nào nói cũng được). KHÔNG hô một từ trống không kiểu «Mèo Cam nói: "
            "jump!», KHÔNG xếp các từ tiếng Anh liền nhau thành danh sách, KHÔNG dịch sang tiếng Việt rồi bỏ từ tiếng Anh. "
            "Mỗi từ chỉ giải nghĩa («... nghĩa là ...») tối đa MỘT lần; nghĩa đã hiện trên màn hình.\n"
            "- Cảnh 1 có MỘT câu hỏi hoặc câu cảm gây tò mò (chỉ một). Cảnh 7 nói rõ bài học bằng đúng chữ: "
            + ", ".join(lesson.get("keywords", [])) + ". Cảnh 8 kết thúc vui, các bạn cười và chơi cùng nhau.\n"
            "- Mỗi cảnh ghi `characters`: 1-2 nhân vật CÓ MẶT trong hình (khoá: " + keys + "), và `action_en`: MỘT câu "
            f"TIẾNG ANH (English only, không một chữ tiếng Việt), tối đa 25 từ, gọi nhân vật bằng tên tiếng Anh ({names_en}), "
            "tả hành động + cảm xúc + đồ vật trong hình, thì hiện tại; ví dụ: «White Rabbit crying and holding a red ball, "
            "Orange Cat looking sorry». Không tả ngoại hình hay quần áo (đã có sẵn), không chữ, không bảng hiệu.\n"
            f"- `title`: tiêu đề tiếng Việt 20-{story_mod.TITLE_MAX} ký tự, gợi tò mò, có tên {main['name_vi']}; chỉ kèm một từ "
            "tiếng Anh hôm nay nếu đọc lên tự nhiên (ví dụ: «Thỏ Trắng và lời promise bị quên»).\n"
            "- `description`: 2-3 câu cho ba mẹ: truyện dạy bé điều gì, kể các từ tiếng Anh kèm nghĩa.\n"
            "- `hashtags`: 2 hashtag tiếng Việt không dấu theo bài học (ví dụ: #giuloihua, #bengoan).\n")
    if feedback:
        text += ("\nBản trước chưa đạt vì:\n" + "\n".join(f"- {p}" for p in feedback)
                 + "\nViết lại TOÀN BỘ truyện, sửa đúng các lỗi trên và giữ ý hay. Kiểm lại trước khi trả: đủ 8 cảnh, mỗi "
                 "từ tiếng Anh đều có mặt trong lời thoại, action_en bằng tiếng Anh, cảnh 1 có câu hỏi/câu cảm.")
    return text


def messages(brief: dict, data: dict, channel: dict, feedback: list[str] | None = None) -> list[dict[str, str]]:
    return [{"role": "system", "content": system_prompt(channel)},
            {"role": "user", "content": user_prompt(brief, data, feedback)}]


def schema(data: dict) -> dict:
    """JSON schema cho grammar của llama.cpp: model không thể trả JSON hỏng hay thiếu trường."""
    keys = list(data["characters"])
    scene = {"type": "object",
             # minLength ép độ dài từng cảnh ngay trong grammar (đo thật: model viết 9-11 chữ/cảnh khi chỉ nhắc bằng lời)
             "properties": {"text": {"type": "string", "minLength": 80, "maxLength": 230},
                            "characters": {"type": "array", "items": {"type": "string", "enum": keys},
                                           "minItems": 1, "maxItems": 2},
                            "action_en": {"type": "string", "minLength": 10, "maxLength": 300}},
             "required": ["text", "characters", "action_en"], "additionalProperties": False}
    return {"type": "object",
            "properties": {"title": {"type": "string", "minLength": 6, "maxLength": story_mod.TITLE_MAX + 10},
                           "scenes": {"type": "array", "items": scene, "minItems": story_mod.N_SCENES,
                                      "maxItems": story_mod.N_SCENES},
                           "description": {"type": "string", "maxLength": 1200},
                           "hashtags": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 4}},
            "required": ["title", "scenes", "description", "hashtags"], "additionalProperties": False}


def image_prompt(scene: dict, brief: dict, data: dict, idx: int = 0) -> str:
    """[hành động model viết (tiếng Anh; không phải tiếng Anh thì dùng hành động dự phòng theo vai cảnh idx)] + [mô tả nhân
    vật nguyên văn, áo màu gì nói trước] + [bối cảnh + thế giới] + [phong cách cố định]. Giữ ngắn cho vừa 256 token T5."""
    chars = data["characters"]
    keys = [k for k in scene.get("characters", []) if k in chars][:2] or [brief["main"]]
    action = (scene.get("action_en") or "").strip().rstrip(".")
    if not story_mod.is_english(action) or len(action.split()) < 3:
        names = " and ".join(chars[k]["name_en"] for k in keys)
        action = f"{names} {FALLBACK_ACTIONS_EN[idx % len(FALLBACK_ACTIONS_EN)]}"
    descs = ". ".join(chars[k]["prompt_en"] for k in keys)
    setting = story_mod.by_id(data["settings"], brief["setting"]).get("en", "")
    # «exactly»: đo thật 18/11, FLUX hay vẽ thêm bản sao nhân vật (hai thỏ, hai sóc) hoặc một con gấu con lạ
    who = "Exactly two friends in the picture" if len(keys) == 2 else "Exactly one character in the picture"
    return f"{who}: {action}. {descs}. Setting: {setting}, {data['world']['prompt_en']}. {STYLE_EN}, no other animals"


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
