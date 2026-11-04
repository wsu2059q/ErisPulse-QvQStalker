"""H8 few-shot 双池：反响准入 / 基准池封池 / 增量池周轮换 / 样例合并。"""
import time

from QvQStalker.fewshot import FewShotPools, group_of, qualified_text

from fakes import FakeStorage


def make_pools(**kw):
    storage = FakeStorage()
    return FewShotPools(storage, base_cap=kw.get("base", 3), inc_cap=kw.get("inc", 5))


def test_qualified_text_gates():
    assert qualified_text("草，这也太离谱了") is True
    assert qualified_text("a") is False                       # 太短
    assert qualified_text("长" * 80) is False                  # 太长
    assert qualified_text("看这个 https://x.com/y") is False   # 链接
    assert qualified_text("```python\nprint(1)\n```") is False  # 代码块


def test_admission_requires_reaction_in_window():
    pools = make_pools()
    t0 = time.time()
    assert pools.record_bot_reply("onebot11:group:1", "这波属实没想到", ts=t0) is True
# 5 分钟外才回应 → 不入池
    assert pools.record_reaction("onebot11:group:1", ts=t0 + 400) is False
    assert pools._get("QvQStalker:fewshot:base", []) == []
    assert pools._get("QvQStalker:fewshot:inc:1", []) == []
# 重新 pending，窗口内回应 → 入池（基准池与增量池都有记录）
    pools.record_bot_reply("onebot11:group:1", "绷不住了", ts=t0)
    assert pools.record_reaction("onebot11:group:1", ts=t0 + 60) is True
    stored = [e["text"] for e in pools._get("QvQStalker:fewshot:base", [])] +              [e["text"] for e in pools._get("QvQStalker:fewshot:inc:1", [])]
    assert "绷不住了" in stored


def test_base_pool_seals_when_full():
    pools = make_pools(base=2, inc=0)
    now = time.time()
    for i in range(4):
        pools.record_bot_reply("onebot11:group:1", f"样例句{i}", ts=now + i)
        pools.record_reaction("onebot11:group:1", ts=now + i + 10)
    base = pools._get("QvQStalker:fewshot:base", [])
    assert [e["text"] for e in base] == ["样例句0", "样例句1"]  # 封池：只留最先两条
    assert pools.status()["base_full"] is True


def test_incremental_pool_weekly_rotation():
    pools = make_pools(base=0, inc=5)
    now = time.time()
    pools._set("QvQStalker:fewshot:inc:1", [
        {"text": "旧句子", "chat_key": "onebot11:group:1", "ts": now - 8 * 86400},
    ])
    pools.record_bot_reply("onebot11:group:1", "新鲜句子", ts=now)
    pools.record_reaction("onebot11:group:1", ts=now + 1)
    inc = pools._get("QvQStalker:fewshot:inc:1", [])
    assert [e["text"] for e in inc] == ["新鲜句子"]   # 7 天外的旧句被裁掉


def test_examples_merge_base_and_group_with_threshold():
    pools = make_pools(base=3, inc=5)
    now = time.time()
    pools._set("QvQStalker:fewshot:base", [
        {"text": f"基准{i}", "chat_key": "x", "ts": now} for i in range(3)
    ])
    pools._set("QvQStalker:fewshot:inc:1", [
        {"text": "增量句", "chat_key": "onebot11:group:1", "ts": now}
    ])
    out = pools.examples_for("onebot11:group:1")
    assert "基准0" in out and "增量句" in out
# 样例不足 3 条 → 不注入
    pools2 = make_pools()
    pools2._set("QvQStalker:fewshot:base", [{"text": "孤句", "chat_key": "x", "ts": now}])
    assert pools2.examples_for("onebot11:group:1") == []


def test_isolated_session_maps_to_group_pool():
    assert group_of("onebot11:group:123:u456") == "123"
    assert group_of("onebot11:private:456") == "onebot11:private:456"
