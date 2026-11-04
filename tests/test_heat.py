"""话题热度公式各因子 / 衰减 / 上限 / 会话隔离。"""

import pytest

from QvQStalker.heat import TopicHeat


def test_first_message_base_delta():
    heat = TopicHeat()
    assert heat.update("g1", "普通消息", now=1000.0) == pytest.approx(0.05)


def test_question_and_exclamation_bonus():
    heat = TopicHeat()
    assert heat.update("g1", "这是什么？", now=1000.0) == pytest.approx(0.05 + 0.1)
    assert heat.update("g2", "太好了!", now=1000.0) == pytest.approx(0.05 + 0.05)
    assert heat.update("g3", "什么情况？！", now=1000.0) == pytest.approx(0.05 + 0.1 + 0.05)


@pytest.mark.parametrize("gap,bonus", [
    (3.0, 0.15),   # <5s
    (10.0, 0.08),  # <15s
    (20.0, 0.03),  # <30s
    (40.0, 0.0),   # >=30s 无频率加成
])
def test_message_frequency_bonus(gap, bonus):
    heat = TopicHeat()
    heat.update("g1", "hi", now=1000.0)  # 首条 0.05
    # 第二条：先按 gap 衰减再累加 0.05 + bonus
    decay = min(gap / 60.0, 1.0) * 0.5
    expected = 0.05 * (1 - decay) + 0.05 + bonus
    assert heat.update("g1", "hi", now=1000.0 + gap) == pytest.approx(expected)


def test_long_gap_decays_existing_heat():
    heat = TopicHeat()
    heat.update("g1", "hi", now=1000.0)  # 建立时间基准
    heat.set("g1", 1.0)                  # 直接抬高热度（不改时间基准）
    # gap 120s → 衰减比例 min(120/60,1)*0.5 = 0.5 → 1.0*0.5 + 0.05
    assert heat.update("g1", "hi", now=1120.0) == pytest.approx(0.55)


def test_heat_cap():
    heat = TopicHeat(cap=2.0)
    heat.set("g1", 1.95)
    # set 不写 _last_ts → 视为首条：1.95 + 0.05 + 0.1 超上限 → 封顶
    assert heat.update("g1", "快问快答？", now=2000.0) == pytest.approx(2.0)


def test_cap_zero_or_negative_falls_back_default():
    heat = TopicHeat(cap=0)
    assert heat._cap == TopicHeat.DEFAULT_CAP


def test_chat_isolation():
    heat = TopicHeat()
    heat.update("g1", "这是什么？", now=1000.0)
    assert heat.get("g2") == 0.0
    assert heat.get("g1") == pytest.approx(0.15)
    assert sorted(heat.keys()) == ["g1"]


def test_reset_clears_state():
    heat = TopicHeat()
    heat.update("g1", "hi", now=1000.0)
    heat.reset("g1")
    assert heat.get("g1") == 0.0
    # reset 后再 update 视为首条（无频率加成/无衰减）
    assert heat.update("g1", "hi", now=1000.5) == pytest.approx(0.05)
