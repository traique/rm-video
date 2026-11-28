"""1.6.2: kênh YouTube truyện bé khai «dành cho trẻ em» theo cài đặt KIDS_YT_MADE_FOR_KIDS (mặc định không — khai có thì
YouTube gần như không đề xuất, video không có lượt xem); tiêu đề YouTube không còn gắn «#Shorts»."""
import re

import config
import prompt_engine
from rmvideo.app import settings, youtube
from rmvideo.kids import prompts


def test_setting_schema_default_off():
    s = settings.BY_KEY["KIDS_YT_MADE_FOR_KIDS"]
    assert s.default == "0" and s.group == "kids" and s.live and {v for v, _ in s.options} == {"0", "1"}
    assert s.check("1") == "1" and s.check("0") == "0"
    try:
        s.check("yes")
    except ValueError:
        pass
    else:
        raise AssertionError("chỉ nhận 0 / 1")
    assert config.EDITABLE["KIDS_YT_MADE_FOR_KIDS"] == "0"


def test_made_for_kids_follows_setting_and_cook_never(monkeypatch):
    monkeypatch.setattr(config, "KIDS_YT_MADE_FOR_KIDS", "0")
    assert youtube.made_for_kids("kids") is False
    assert youtube.video_body("kids", "T", "D", None, 0.0)["status"]["selfDeclaredMadeForKids"] is False
    monkeypatch.setattr(config, "KIDS_YT_MADE_FOR_KIDS", "1")
    assert youtube.made_for_kids("kids") is True
    assert youtube.video_body("kids", "T", "D", None, 0.0)["status"]["selfDeclaredMadeForKids"] is True
    assert youtube.made_for_kids("cook") is False                                   # kênh bếp: luôn không
    assert youtube.video_body("cook", "T", "D", None, 0.0)["status"]["selfDeclaredMadeForKids"] is False


def test_saved_setting_applies(monkeypatch, tmp_path):
    """Lưu trên web (settings.json tạm) -> lượt tải lên sau đọc ngay, không cần khởi động lại."""
    orig = config.SETTINGS_FILE
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "s.json"))
    try:
        config.save_settings({"KIDS_YT_MADE_FOR_KIDS": "1"})
        assert youtube.made_for_kids("kids") is True
        config.save_settings({"KIDS_YT_MADE_FOR_KIDS": "0"})
        assert youtube.made_for_kids("kids") is False
    finally:
        config.SETTINGS_FILE = orig
        config._apply()


def test_youtube_title_line_has_no_shorts_tag():
    post = prompt_engine.platform_post("Bò kho mềm", "Thơm lừng #bokho", "bokho, monngon", "Nguồn: @a",
                                       recipe="🧂 Nguyên liệu: bò")
    yt = post.split("▶ YouTube Shorts:\n", 1)[1]
    assert yt.split("\n", 1)[0] == "Bò kho mềm" and "#Shorts" not in post
    assert youtube.texts(post) == ("Bò kho mềm", youtube.clean_desc(yt.split("\n", 1)[1]))


def test_old_posts_and_queued_titles_drop_shorts_tag():
    # bài làm trước 1.6.2 (dòng tiêu đề còn «#Shorts») và mục đang chờ tải lên: bỏ khi tải
    old = "📌 Bò kho\n\n▶ Facebook:\nNgon\n\n▶ YouTube Shorts:\nBò kho #Shorts\n\nNgon\n\n#bokho"
    assert youtube.texts(old)[0] == "Bò kho"
    assert youtube.video_body("cook", "Canh chua #Shorts", "D", None, 0.0)["snippet"]["title"] == "Canh chua"
    for raw, want in (("#Shorts Gà rang", "Gà rang"), ("Gà #SHORTS rang", "Gà rang"), ("#Shorts", "Video mới"),
                      ("Gà rang #shortsvn", "Gà rang #shortsvn")):            # chỉ bỏ đúng thẻ #Shorts
        assert youtube.clean_title(raw) == want
    long = youtube.clean_title("Truyện " + "rất hay " * 20 + "#Shorts")
    assert len(long) <= youtube.TITLE_MAX and long.endswith("…") and "#" not in long


def test_kids_title_tag_uses_full_100_chars():
    from test_kids import DATA, _good_story
    import random
    from rmvideo.kids import story
    brief = story.pick(DATA, [], random.Random(2))
    tag = "Truyện kể bé nghe, học tiếng Anh"
    # tiêu đề vừa khít 100 ký tự khi nối đuôi: trước 1.6.2 phải chừa 8 ký tự cho « #Shorts» nên không nối
    st = dict(story.normalize(_good_story(brief), DATA), title="T" * (prompts.YT_TITLE_MAX - len(tag) - 3))
    title, _, _ = prompts.post_pack(st, brief, {"title_tag": tag}, DATA)
    assert title.endswith(f" | {tag}") and len(title) == prompts.YT_TITLE_MAX
    assert youtube.clean_title(title) == title


def test_hashtag_budget_without_shorts():
    tags = " ".join(f"#t{i}" for i in range(20))
    yt = prompt_engine.platform_post("T", "Mô tả", tags, fb_tags=6, yt_tags=20).split("▶ YouTube Shorts:\n", 1)[1]
    assert len(re.findall(r"#\w+", yt)) == prompt_engine.YT_HASHTAG_MAX                 # 15 thẻ thật, không mất chỗ cho #Shorts
