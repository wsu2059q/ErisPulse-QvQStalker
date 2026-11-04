"""Main 模块：services 白名单、生命周期、观察回调（绝不 event.done）、四服务、speak 降级。"""

import datetime as dt
import inspect
import random
import time

import pytest

from QvQStalker.Main import OBSERVE_EVENT, Main

from fakes import FakeSDK, make_config

GKEY = "onebot11:group:100"


def make_main(sdk=None, **cfg_overrides):
    main = Main()
    main.sdk = sdk if sdk is not None else FakeSDK()
    main.logger = main.sdk.logger.get_child("QvQStalker")
    main._local_cfg = make_config(**cfg_overrides)
    return main


def meta_services():
    meta = Main.get_meta()
    services = meta.services if not isinstance(meta, dict) else meta["services"]
    return [s["name"] for s in services]


# ==================== 元信息（services 白名单必须列全） ====================

def test_get_meta_lists_all_services():
    assert meta_services() == [
        "qvq_decision", "group_heat", "urge_status", "request_speak", "fewshot_status",
        "qvq_settings", "qvq_schemas", "qvq_access", "qvq_extensions",
    ]


def test_get_meta_shapes():
    meta = Main.get_meta()
    data = meta if isinstance(meta, dict) else meta.to_dict()
    assert data["name"] == "QvQStalker"
    assert data["version"]
    assert data["min_sdk_version"] == "2.8.0"


# ==================== 生命周期 ====================

async def test_on_load_subscribes_observe_and_starts_loop():
    sdk = FakeSDK()
    main = make_main(sdk, check_interval_minutes=30)
    assert await main.on_load({"module_name": "QvQStalker"}) is True
    assert (OBSERVE_EVENT, main._on_observe) in sdk.lifecycle.registered
    assert main.proactive is not None
    task = main.proactive._task
    assert task is not None and not task.done()
    await main.on_unload({"module_name": "QvQStalker"})
    assert task.done() or task.cancelled()
    assert (OBSERVE_EVENT, main._on_observe) in sdk.lifecycle.unregistered


async def test_on_load_survives_missing_lifecycle():
    class BrokenSDK(FakeSDK):
        def __init__(self):
            super().__init__()
            del self.lifecycle

    main = make_main(BrokenSDK())
    assert await main.on_load({}) is True  # 订阅失败不阻断加载
    await main.on_unload({})


# ==================== 观察回调：只入窗，绝不 event.done ====================

async def test_on_observe_feeds_window_heat_urge():
    sdk = FakeSDK()
    main = make_main(sdk)
    await main.on_load({})
    ts = time.time()
    await main._on_observe({
        "chat_key": GKEY, "platform": "onebot11", "detail_type": "group",
        "group_id": "100", "sender_id": "u1", "sender_name": "小明",
        "text": "这是什么？", "is_group": True, "at_bot": False, "ts": ts,
    })
    assert main.window.count(GKEY) == 1
    assert main.heat.get(GKEY) == pytest.approx(0.15)  # 0.05 + 问号 0.1
    # 冲动值 = 基础 0.06 + 提问 0.12 + 热度加成 min(0.15*0.05, 0.10)
    assert main.urge.get(GKEY, now=ts + 0.5) == pytest.approx(0.06 + 0.12 + 0.0075, abs=1e-3)
    await main.on_unload({})


async def test_on_observe_never_raises_and_ignores_private():
    sdk = FakeSDK()
    main = make_main(sdk)
    await main.on_load({})
    # 载荷是纯数据 dict（没有 done 属性）：回调只读、任何垃圾输入都不抛错
    for payload in (None, "str", 123, {}, {"text": "no key"},
                    {"chat_key": "onebot11:private:u1", "text": "hi", "is_group": False},
                    {"chat_key": GKEY, "text": "ok", "ts": "bad", "is_group": True}):
        await main._on_observe(payload)
    assert main.window.chat_keys() == [GKEY]  # 私聊未入窗
    assert main.window.count(GKEY) == 1
    await main.on_unload({})


async def test_on_observe_via_fake_lifecycle_emit():
    sdk = FakeSDK()
    main = make_main(sdk)
    await main.on_load({})
    await sdk.lifecycle.emit(OBSERVE_EVENT, {"chat_key": GKEY, "text": "热热闹闹",
                                             "is_group": True, "ts": 123.0})
    assert main.window.last_ts(GKEY) == 123.0
    await main.on_unload({})


# ==================== 对外服务 ====================

async def test_qvq_decision_service_delegates():
    main = make_main(question_prob=1.0)
    await main.on_load({})
    result = await main.qvq_decision(ctx={
        "chat_key": GKEY, "text": "这是什么？", "is_group": True,
    })
    assert result["decision"] == "speak"
    assert set(result) == {"decision", "reason", "score", "urgency"}
    await main.on_unload({})


async def test_group_heat_and_urge_status_services():
    main = make_main()
    await main.on_load({})
    await main._on_observe({"chat_key": GKEY, "text": "这是什么？",
                            "is_group": True, "ts": time.time()})
    heat = await main.group_heat(GKEY)
    assert heat == {"chat_key": GKEY, "heat": 0.15}
    all_heat = await main.group_heat()
    assert GKEY in all_heat["chats"]
    urge = await main.urge_status(GKEY)
    assert urge["chat_key"] == GKEY and urge["urge"] > 0 and urge["threshold"] == 1.0
    all_urge = await main.urge_status()
    assert GKEY in all_urge["chats"]
    await main.on_unload({})


async def test_request_speak_ok_and_degrade():
    # speak 存在 → ok
    sdk = FakeSDK(handlers={("QvQChat", "speak"): lambda **kw: {"ok": True}})
    main = make_main(sdk)
    await main.on_load({})
    result = await main.request_speak(chat_key=GKEY, reason="测试", prompt="摘要")
    assert result["ok"] is True
    call = sdk.module.calls[-1]
    assert call[0] == "QvQChat" and call[1] == "speak"
    assert call[2]["chat_key"] == GKEY
    assert call[2]["reason"].startswith("[QvQStalker]")
    assert call[2]["prompt"] == "摘要"
    # speak 不存在（未实现）→ ok=False + warning，不抛异常
    sdk2 = FakeSDK()
    main2 = make_main(sdk2)
    await main2.on_load({})
    result2 = await main2.request_speak(chat_key=GKEY)
    assert result2["ok"] is False
    assert "error" in result2
    assert any("speak" in m for m in sdk2.logger.of_level("warning"))
    assert (await main.request_speak(chat_key=""))["ok"] is False
    await main.on_unload({})
    await main2.on_unload({})


async def test_judge_uses_qvqchat_chat_with_model_fallback():
    calls = []

    async def chat_handler(**kwargs):
        calls.append(kwargs)
        if "model" in kwargs:
            raise TypeError("unexpected keyword 'model'")
        return "是"

    sdk = FakeSDK(handlers={("QvQChat", "chat"): chat_handler})
    main = make_main(sdk, llm_judge=True, judge_model="gpt-4o-mini",
                     default_probability=0.0, min_messages_between_replies=0,
                     heat_flag_threshold=0.5)
    await main.on_load({})
    main.heat.set(GKEY, 1.0)
    random.seed(1)  # L2 热度标志掷 0.134 < 0.3 → 升级 L5
    result = await main.qvq_decision(ctx={"chat_key": GKEY, "text": "聊聊",
                                          "is_group": True})
    assert result["decision"] == "speak"  # L2 升级 + L5 判断「是」
    assert any("model" in c for c in calls)      # 第一次带 judge_model
    assert any("model" not in c for c in calls)  # TypeError 后去掉重试
    await main.on_unload({})


# ==================== 主动发言：门槛链与降级 ====================

def prime_chat(main, chat_key=GKEY, urge=1.5, messages=2, ts=1000.0):
    for i in range(messages):
        main.window.add(chat_key, f"旁听{i}", sender_id="u1", ts=ts + i)
    main.urge.set(chat_key, urge)


async def test_proactive_check_speaks_when_all_gates_pass():
    async def speak_ok(**kw):
        return {"ok": True}

    sdk = FakeSDK(handlers={("QvQChat", "speak"): speak_ok})
    main = make_main(sdk, sleep_hour=-1, wake_hour=-1)  # 作息不限制（免时区影响）
    await main.on_load({})
    prime_chat(main)
    result = await main.proactive.check(now=2000.0)
    assert result["spoke"] is True and result["chat_key"] == GKEY
    # 成功后：冲动清零 + 计数 + last_speak 记录
    assert main.urge.get(GKEY) == 0.0
    assert main.sdk.storage.get("QvQStalker:proactive:last_speak", {}).get(GKEY) == 2000.0
    daily = main.sdk.storage.get(
        "QvQStalker:proactive:daily:" + dt.datetime.fromtimestamp(2000.0).strftime("%Y-%m-%d"), {})
    assert daily["global"] == 1 and daily["chats"][GKEY] == 1
    # 再来一轮：沉寂门槛（距上次开口 < min_silence_hours）拦下
    prime_chat(main, urge=1.5)
    result2 = await main.proactive.check(now=2000.0 + 60.0)
    assert result2["spoke"] is False
    assert result2["skipped"][GKEY] == "silence_gate"
    await main.on_unload({})


async def test_proactive_degrades_when_speak_missing():
    sdk = FakeSDK()  # 无 QvQChat.speak handler → call 抛错
    main = make_main(sdk, sleep_hour=-1, wake_hour=-1)
    await main.on_load({})
    prime_chat(main)
    result = await main.proactive.check(now=2000.0)
    assert result["spoke"] is False
    # 降级：warning 日志 + 冲动减半 + 不消耗每日配额
    assert any("speak" in m for m in sdk.logger.of_level("warning"))
    assert main.urge.get(GKEY) == pytest.approx(0.75)
    assert all("QvQStalker:proactive:daily" not in k for k in sdk.storage.keys())
    await main.on_unload({})


async def test_proactive_gate_chain():
    sdk = FakeSDK(handlers={("QvQChat", "speak"): lambda **kw: {"ok": True}})
    main = make_main(sdk, sleep_hour=-1, wake_hour=-1)  # 作息不限制
    await main.on_load({})

    # 冲动值不足
    prime_chat(main, urge=0.5)
    r = await main.proactive.check(now=2000.0)
    assert r["spoke"] is False and r["checked"] == 0

    # 全局每日上限
    main.urge.set(GKEY, 1.5)
    date = dt.datetime.fromtimestamp(2000.0).strftime("%Y-%m-%d")
    sdk.storage.set(f"QvQStalker:proactive:daily:{date}", {"global": 3, "chats": {}})
    r = await main.proactive.check(now=2000.0)
    assert r["spoke"] is False and r["skipped"]["*"] == "global_daily_limit"

    # 未回复冷却：距上次开口 ≥ 沉寂门槛但 < 冷却时长，且窗口内无开口后的新消息
    sdk.storage.set(f"QvQStalker:proactive:daily:{date}", {"global": 0, "chats": {}})
    sdk.storage.set("QvQStalker:proactive:last_speak", {GKEY: 1000.0})
    main.window.clear()
    main.window.add(GKEY, "旧消息", ts=900.0)  # 早于上次开口 → 未回复
    main.urge.set(GKEY, 1.5)
    r = await main.proactive.check(now=1000.0 + 25000.0)  # 6.9h：过沉寂门槛、仍在 12h 冷却内
    assert r["spoke"] is False and r["skipped"][GKEY] == "unreplied_cooldown"
    await main.on_unload({})


async def test_proactive_is_asleep():
    main = make_main(sleep_hour=1, wake_hour=3)
    await main.on_load({})
    # 用显式 now 构造本地 2 点 / 4 点，避免时区不确定性
    local_two = dt.datetime.now().replace(hour=2, minute=0, second=0, microsecond=0).timestamp()
    assert main.proactive.is_asleep(main._cfg(), local_two) is True
    local_four = dt.datetime.now().replace(hour=4, minute=0, second=0, microsecond=0).timestamp()
    assert main.proactive.is_asleep(main._cfg(), local_four) is False
    # sleep == wake → 不限制
    main._local_cfg = make_config(sleep_hour=2, wake_hour=2)
    assert main.proactive.is_asleep(main._cfg(), local_two) is False
    await main.on_unload({})


async def test_loop_interval_respects_jitter_floor():
    main = make_main(check_interval_minutes=0)  # 误配成 0 → 下限 300s 兜底
    await main.on_load({})
    interval = main.proactive._next_interval()
    assert interval >= 300.0
    await main.on_unload({})


async def test_services_usable_without_on_load():
    main = make_main()
    result = await main.qvq_decision({"chat_key": GKEY, "text": "x", "is_group": True})
    assert set(result) == {"decision", "reason", "score", "urgency"}
    assert await main.group_heat(GKEY) == {"chat_key": GKEY, "heat": 0.0}
    assert (await main.urge_status())["chats"] == {}


async def test_observe_handler_is_async_and_payload_untouched():
    main = make_main()
    payload = {"chat_key": GKEY, "text": "原始", "is_group": True, "ts": 5.0}
    snapshot = dict(payload)
    await main._on_observe(payload)
    assert payload == snapshot  # 回调不修改载荷（供其它订阅者使用）
    assert inspect.iscoroutinefunction(main._on_observe)


async def test_qvq_conventions_schemas_access_extensions():
    main = make_main()
    schemas = await main.qvq_schemas()
    assert "request_speak" in schemas and schemas["request_speak"]["required"] == ["chat_key"]
    access = await main.qvq_access()
    assert access["request_speak"] == "admin" and access["group_heat"] == "safe"
    ext = await main.qvq_extensions()
    assert ext["prompts"] and "request_speak" in ext["prompts"][0]["text"]
