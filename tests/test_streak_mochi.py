from datetime import date, timedelta

from meow import mochi, streak

D = date(2026, 10, 14)  # a Wednesday


def days(*offsets):
    return {D - timedelta(days=o) for o in offsets}


def test_streak_counts_back_from_today_or_yesterday():
    assert streak.compute(days(0, 1, 2), D).current == 3
    s = streak.compute(days(1, 2, 3), D)  # today not logged yet: still alive
    assert s.current == 3 and not s.logged_today
    assert streak.compute(days(2, 3), D).current == 0  # yesterday missed, no run before it to bridge? see freeze rule


def test_freeze_bridges_one_missed_day_after_a_good_week():
    logged = days(0, 1, 2, 4, 5, 6, 7, 8, 9)  # day 3 missed (Sun 11 Oct)
    s = streak.compute(logged, D)
    assert s.current == 9 and s.freezes_used == [D - timedelta(days=3)]


def test_freeze_needs_five_of_seven_and_one_per_week():
    assert streak.compute(days(0, 1, 2, 4, 5), D).current == 3  # only 2 logged before the gap
    # two gaps in the same Mon-Sun week (Tue 13 and Thu 15 Oct): only the later one is bridged
    sat = date(2026, 10, 17)
    logged = {sat - timedelta(days=o) for o in (0, 1, 3, 5, 6, 7, 8, 9, 10, 11, 12)}
    s = streak.compute(logged, sat)
    assert s.current == 3 and s.freezes_used == [date(2026, 10, 15)]


def test_milestones_and_accessories():
    assert streak.milestone_reached(6, 7) == 7
    assert streak.milestone_reached(7, 8) is None
    assert streak.accessory(8) == "🔔" and streak.accessory(30) == "🧣" and streak.accessory(100) == "👑"
    assert streak.best_run(days(0, 1, 5, 6, 7, 8)) == 4


def test_bowl_and_results():
    assert mochi.bowl(90000, date(2026, 10, 1)) == 2903  # S$900 / 31 days
    b = 3000
    assert mochi.result_for(0, b, True) == "no_spend"
    assert mochi.result_for(1500, b, True) == "half"
    assert mochi.result_for(3000, b, True) == "within"
    assert mochi.result_for(4500, b, True) == "over"
    assert mochi.result_for(4501, b, True) == "splurge"
    assert mochi.result_for(0, b, False) == "silent"


def test_weight_is_capped_and_clamped():
    assert mochi.score(50, False, "no_spend", []).weight == 53
    assert mochi.score(99, False, "no_spend", []).weight == 100
    s = mochi.score(2, False, "splurge", [])
    assert s.weight == 0 and s.away and s.left


def test_grandmas_house_and_return():
    assert mochi.score(0, True, "half", ["within"]).away  # only 2 good days
    back = mochi.score(0, True, "half", ["within", "no_spend"])
    assert back.came_back and back.weight == mochi.RETURN_WEIGHT and not back.away
    assert mochi.score(0, True, "half", ["over", "within"]).away  # a bad day in the last 3


def test_stages_moods_and_status_line():
    assert [mochi.stage(w) for w in (5, 25, 50, 75, 95)] == ["Skinny", "Thin", "Healthy", "Chubby", "Chonky King"]
    assert mochi.mood(0, 3000, True) == "😻" and mochi.mood(2000, 3000, False) == "😸"
    assert mochi.mood(4000, 3000, False) == "😿" and mochi.mood(9000, 3000, False) == "🙀"
    line = mochi.status_line(62, False, 1770, 3000, "SGD", accessory="🔔", streak=29)
    assert line == "😸🔔 Mochi 62/100 ▓▓▓▓▓▓░░░░ Healthy · bowl S$12.30 left · 🔥 29"
    assert "over by S$5.00" in mochi.status_line(50, False, 3500, 3000, "SGD")
    assert "grandma" in mochi.status_line(0, True, 0, 3000, "SGD")
