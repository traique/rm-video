"""Lời nhắc cho model chữ (llama.cpp trên Kaggle) và cách ghép prompt ảnh từ character bible — thuần chuỗi.

Model KHÔNG được đụng khối mô tả nhân vật và khối phong cách của prompt ảnh: model chỉ viết `action_en` (hành động trong
hình, 1-2 nhân vật); mã ghép [nhân vật nguyên văn từ bible] + [hành động] + [bối cảnh + thế giới] + [phong cách cố định].
Đó là cách rẻ nhất để 8 ảnh cùng một chất và nhân vật đỡ đổi dáng."""
from __future__ import annotations

from rmvideo.kids import story as story_mod

STYLE_EN = ("Pixar style 3D animation, cute children's storybook illustration, soft rounded shapes, big expressive eyes, "
            "bright cheerful colors, soft warm lighting, clean simple background, vertical 9:16 composition, "
            "same character design, same clothes, consistent appearance, no text, no letters")

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
    roles = "\n".join(f"  Cảnh {i}: {r}" for i, r in enumerate(story_mod.SCENE_ROLES, 1))
    keys = ", ".join(chars)
    text = (f"Viết một truyện ngắn xảy ra ở {data['world']['name']}, nơi bốn bạn nhỏ sống cùng nhau: {cast}.\n"
            f"Nhân vật chính hôm nay: {main['name_vi']}.\n"
            f"Bối cảnh: {setting['vi']}. Chuyện xảy ra: {main['name_vi']} {conflict['vi']}.\n"
            f"Bài học của truyện: {lesson['vi']}.\n"
            f"Từ tiếng Anh bé học hôm nay: {words}.\n\n"
            f"Cấu trúc đúng {story_mod.N_SCENES} cảnh, theo thứ tự:\n{roles}\n"
            f"Yêu cầu:\n"
            f"- Mỗi cảnh {story_mod.SCENE_MIN + 5}-{story_mod.SCENE_MAX - 8} chữ; cả truyện "
            f"{story_mod.WORDS_MIN + 5}-{story_mod.WORDS_MAX - 10} chữ.\n"
            "- Cả bốn bạn đều phải được nhắc tên (gọi đúng tên: "
            + ", ".join(c["name_vi"] for c in chars.values()) + ").\n"
            f"- Mỗi từ tiếng Anh xuất hiện ít nhất {story_mod.EN_REPEAT} lần trong lời kể / lời thoại, viết đúng chính tả "
            "tiếng Anh, chữ thường; lần đầu kèm nghĩa (ví dụ: «sorry» nghĩa là xin lỗi), các lần sau dùng tự nhiên trong "
            "lời thoại (ví dụ: Thỏ Trắng nói: Sorry Gấu Nâu ơi!).\n"
            "- Cảnh 1 có một câu hỏi hoặc câu cảm gây tò mò. Cảnh 7 nói rõ bài học bằng đúng chữ: "
            + ", ".join(lesson.get("keywords", [])) + ". Cảnh 8 kết thúc vui, các bạn cười và chơi cùng nhau.\n"
            "- Mỗi cảnh ghi `characters`: 1-2 nhân vật CÓ MẶT trong hình (khoá: " + keys + "), và `action_en`: MỘT câu "
            "tiếng Anh ngắn (tối đa 25 từ) tả hành động và cảm xúc trong hình, thì hiện tại, không tả ngoại hình hay "
            "quần áo (đã có sẵn), không chữ, không bảng hiệu.\n"
            f"- `title`: tiêu đề tiếng Việt 20-{story_mod.TITLE_MAX} ký tự, gợi tò mò, có tên {main['name_vi']} và một từ "
            "tiếng Anh hôm nay.\n"
            "- `description`: 2-3 câu cho ba mẹ: truyện dạy bé điều gì, kể các từ tiếng Anh kèm nghĩa.\n"
            "- `hashtags`: 2 hashtag tiếng Việt không dấu theo bài học (ví dụ: #biettxinloi, #bexinloi).\n")
    if feedback:
        text += "\nBản trước chưa đạt vì:\n" + "\n".join(f"- {p}" for p in feedback) + "\nHãy viết lại cho đúng mọi yêu cầu."
    return text


def messages(brief: dict, data: dict, channel: dict, feedback: list[str] | None = None) -> list[dict[str, str]]:
    return [{"role": "system", "content": system_prompt(channel)},
            {"role": "user", "content": user_prompt(brief, data, feedback)}]


def schema(data: dict) -> dict:
    """JSON schema cho grammar của llama.cpp: model không thể trả JSON hỏng hay thiếu trường."""
    keys = list(data["characters"])
    scene = {"type": "object",
             "properties": {"text": {"type": "string", "minLength": 40, "maxLength": 400},
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


def image_prompt(scene: dict, brief: dict, data: dict) -> str:
    """[mô tả nhân vật nguyên văn] + [hành động model viết] + [bối cảnh + thế giới] + [phong cách cố định]."""
    chars = data["characters"]
    keys = [k for k in scene.get("characters", []) if k in chars][:2] or [brief["main"]]
    descs = ". ".join(chars[k]["prompt_en"] for k in keys)
    setting = story_mod.by_id(data["settings"], brief["setting"]).get("en", "")
    action = (scene.get("action_en") or "").strip().rstrip(".")
    n = "Two friends" if len(keys) == 2 else "One character"
    return (f"{descs}. {n} in the scene: {action}. Setting: {setting}, {data['world']['prompt_en']}. {STYLE_EN}")


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
