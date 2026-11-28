"""1.6.1: hashtag + từ khoá truyện bé cho Facebook / YouTube — thẻ rộng ba mẹ và bé hay tìm (#hoathinh, #thieunhi, #chobe,
#behoc, #phimhoathinh, #mevabe), thẻ theo nhân vật chính, Facebook 6 thẻ / YouTube 12 (giữ trong giới hạn 15 của YouTube)."""
import random
import re

import config
import kids_auto
import prompt_engine
from rmvideo.app import kids_weekly, youtube
from rmvideo.kids import prompts, story
from test_kids import DATA, _good_story


def _section(post: str, name: str) -> str:
    return post.split(f"▶ {name}:\n", 1)[1].split("\n\n▶ ", 1)[0]


def _story(seed=2):
    brief = story.pick(DATA, [], random.Random(seed))
    return brief, story.normalize(_good_story(brief), DATA)


def test_default_order_and_counts_per_platform():
    brief, st = _story()
    tags = prompts.hashtags(st, brief, kids_auto.channel(), DATA)
    own = st["hashtags"]
    char = "#" + re.sub(r"[^0-9a-z]", "", story._ascii(DATA["characters"][brief["main"]]["name_vi"]).lower())
    assert tags[:6] == ["#truyenchobe", "#hoathinh", own[0], "#thieunhi", "#chobe", "#behoc"]
    assert tags[6:11] == ["#hoctienganhchobe", "#phimhoathinh", "#kebenghe", char, "#mevabe"]
    assert all(t in tags for t in own) and "{truyen}" not in " ".join(tags)
    title, desc, joined = prompts.post_pack(st, brief, kids_auto.channel(), DATA)
    post = prompt_engine.platform_post(title, desc, joined, fb_tags=prompts.FB_TAGS, yt_tags=prompts.YT_TAGS)
    fb = _section(post, "Facebook")
    assert fb.rstrip().endswith(" ".join(tags[:6])) and len(re.findall(r"#\w+", fb)) == 6
    yt = _section(post, "YouTube Shorts")
    assert len(re.findall(r"#\w+", yt)) == 12 <= prompt_engine.YT_HASHTAG_MAX          # 12 thẻ (1.6.2: bỏ #Shorts)
    assert "🔎 truyện cho bé, truyện kể bé nghe, phim hoạt hình cho bé" in fb and "mẹ và bé" in yt
    tt = _section(post, "TikTok")
    assert re.findall(r"#\w+", tt)[:4] == tags[:4]


def test_youtube_never_exceeds_15_hashtags():
    caption = "Mô tả có sẵn #a #b #c #d #e"
    tags = " ".join(f"#t{i}" for i in range(20))
    yt = _section(prompt_engine.platform_post("Tiêu đề", caption, tags, fb_tags=6, yt_tags=12), "YouTube Shorts")
    assert len(re.findall(r"#\w+", yt)) == prompt_engine.YT_HASHTAG_MAX                 # 5 + 10
    crowded = " ".join(f"#c{i}" for i in range(20))
    yt2 = _section(prompt_engine.platform_post("T", crowded, tags, yt_tags=12), "YouTube Shorts")
    assert "#t0" not in yt2                                                            # caption đã quá 15: không thêm


def test_cook_channel_keeps_3_and_8():
    tags = " ".join(f"#m{i}" for i in range(12))
    post = prompt_engine.platform_post("Gà rang", "Ngon lắm", tags)
    assert len(re.findall(r"#\w+", _section(post, "Facebook"))) == 3
    assert len(re.findall(r"#\w+", _section(post, "YouTube Shorts"))) == 8                # 8 (1.6.2: bỏ #Shorts)


def test_custom_hashtags_without_placeholders_keep_old_order():
    brief, st = _story()
    chan = dict(kids_auto.channel(), hashtags="#truyenchobe,#hoctienganhchobe,#kebenghe,#x")
    tags = prompts.hashtags(st, brief, chan, DATA)
    assert tags[:2] == ["#truyenchobe", "#hoctienganhchobe"] and tags[2] == st["hashtags"][0] and tags[-1] == "#x"
    chan2 = dict(chan, hashtags="{nhanvat},#chobe,{truyen}")
    assert prompts.hashtags(dict(st, hashtags=[]), brief, chan2, DATA)[1:] == ["#chobe"]   # truyện không gợi ý thẻ: bỏ chỗ giữ


def test_character_tags():
    got = set()
    for key in DATA["characters"]:
        brief, st = _story()
        tags = prompts.hashtags(dict(st, hashtags=[]), dict(brief, main=key), kids_auto.channel(), DATA)
        got.add(tags[tags.index("#kebenghe") + 1])
    assert got == {"#thotrang", "#gaunau", "#meocam", "#socnho"}


def test_keywords_first_in_youtube_tags_and_fit():
    brief, st = _story()
    title, desc, joined = prompts.post_pack(st, brief, kids_auto.channel(), DATA)
    yt = _section(prompt_engine.platform_post(title, desc, joined, fb_tags=6, yt_tags=12), "YouTube Shorts")
    tags = youtube.video_tags("kids", yt)
    kw = [k.strip() for k in config.KIDS_KEYWORDS.split(",")]
    assert tags[:len(kw)] == kw and {"hoathinh", "thieunhi", "chobe", "behoc", "mevabe"} <= set(tags)
    assert config.KIDS_CHANNEL_NAME in tags
    assert sum(len(t) + (2 if " " in t else 0) + 1 for t in tags) <= youtube.TAGS_MAX_CHARS


def test_weekly_compilation_uses_12_hashtags(monkeypatch):
    clips = [{"title": f"Truyện {k}", "words": ["sun"]} for k in range(3)]
    _, desc = kids_weekly.texts(clips, [80.0, 75.0, 90.0], 1)
    line = desc.strip().splitlines()[-1]
    assert line.split() == [t for t in config.KIDS_HASHTAGS.split(",") if t.startswith("#")][:12]
    assert "#Shorts" not in desc and len(re.findall(r"#\w+", desc)) <= prompt_engine.YT_HASHTAG_MAX
