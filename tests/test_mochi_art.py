"""Mochi's pictures: the right one for her weight, uploaded once, sent with /mochi and the nightly verdict."""
from datetime import datetime, timezone

from meow import mochi


def test_which_picture():
    assert [mochi.art_candidates(w, False)[0] for w in (0, 39, 40, 69, 70, 89)] == \
        ["skinny", "skinny", "neutral", "neutral", "fat", "fat"]
    assert mochi.art_candidates(95, False) == ["king", "fat"]
    assert mochi.art_candidates(50, False, "no_spend") == ["sleeping", "neutral"]
    assert mochi.art_candidates(50, False, "over") == ["sad", "neutral"]
    assert mochi.art_candidates(95, False, "within") == ["king", "fat"]
    assert mochi.art_candidates(80, True) == ["away"]
    for name in ("skinny", "neutral", "fat"):
        assert (mochi.ART_DIR / f"{name}.jpg").stat().st_size > 10_000


def test_new_art_is_used_once_the_file_exists(tmp_path, monkeypatch):
    for name in ("neutral", "fat"):
        (tmp_path / f"{name}.jpg").write_bytes(b"x")
    monkeypatch.setattr(mochi, "ART_DIR", tmp_path)
    assert mochi.art(50, False, "over") == "neutral"     # no sad.jpg yet: falls back to her body
    assert mochi.art(95, False) == "fat"                 # no king.jpg yet
    assert mochi.art(10, True) is None                   # no away.jpg yet: text only
    (tmp_path / "sad.jpg").write_bytes(b"x")
    (tmp_path / "king.jpg").write_bytes(b"x")
    (tmp_path / "away.jpg").write_bytes(b"x")
    assert (mochi.art(50, False, "over"), mochi.art(95, False), mochi.art(10, True)) == ("sad", "king", "away")


def setup(env, weight=50):
    env.say("/start")
    env.say("/budget everyday 600")
    env.conn.execute("update mochi_state set weight = %s", (weight,))


def test_mochi_command_sends_her_picture_and_reuses_the_upload(env):
    setup(env, 50)
    msg = env.say("/mochi")
    assert msg.photo == "neutral.jpg" and "<b>Mochi</b>" in msg.text      # uploaded, card as caption
    assert env.tg.photos[-1] == "<upload neutral.jpg>"
    msg = env.say("/mochi")
    assert env.tg.photos[-1] == "tg-neutral.jpg"                        # Telegram's copy the second time
    env.conn.execute("update mochi_state set weight = 85")
    assert env.say("/mochi").photo == "fat.jpg"
    env.conn.execute("update mochi_state set weight = 12")
    assert env.say("/mochi").photo == "skinny.jpg"


def test_away_at_grandmas_shows_the_empty_cushion(env):
    setup(env, 0)
    env.conn.execute("update mochi_state set away = true")
    msg = env.say("/mochi")
    assert msg.photo == "away.jpg" and "Mochi" in msg.text


def test_all_art_is_in_place():
    for name in ("skinny", "neutral", "fat", "king", "sad", "sleeping", "away"):
        assert (mochi.ART_DIR / f"{name}.jpg").stat().st_size > 10_000, name


def test_picture_failure_falls_back_to_text(env):
    setup(env)
    env.tg.photo_fails = True
    msg = env.say("/mochi")
    assert not hasattr(msg, "photo") and "<b>Mochi</b>" in msg.text


def test_nightly_verdict_comes_with_her_picture(env):
    setup(env, 50)
    env.conn.execute("update mochi_state set started_on = '2026-09-28'")
    env.say("kopi 2 yesterday")                     # S$2 of a S$20 bowl: a great day
    env.conn.commit()
    env.clock.now = datetime(2026, 9, 29, 16, 30, tzinfo=timezone.utc)  # 00:30 on 30 Sep
    before = len(env.tg.sent)
    env.bot.run_tick(env.conn)
    verdicts = [m for m in env.tg.sent[before:] if "Mochi's verdict" in m.text]
    state = env.conn.execute("select weight, away from mochi_state").fetchone()
    last = env.conn.execute("select result from mochi_log order by day desc limit 1").fetchone()["result"]
    expected = mochi.art(state["weight"], state["away"], last)   # e.g. sad.jpg once that art exists
    assert len(verdicts) == 1 and verdicts[0].photo == f"{expected}.jpg" and verdicts[0].silent
    env.bot.run_tick(env.conn)
    assert len([m for m in env.tg.sent[before:] if "Mochi's verdict" in m.text]) == 1   # once
