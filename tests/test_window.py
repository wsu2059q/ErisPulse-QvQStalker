"""旁听窗口：环形淘汰 / 会话隔离 / 间隔与跨度 / 摘要格式 / 并发安全。"""

import threading

from QvQStalker.observe import MessageWindow, group_keys, is_group_key


def test_eviction_keeps_latest():
    win = MessageWindow(size=5)
    for i in range(8):
        win.add("g", f"msg{i}", ts=1000.0 + i)
    assert win.count("g") == 5
    texts = [item["text"] for item in win.recent("g")]
    assert texts == ["msg3", "msg4", "msg5", "msg6", "msg7"]


def test_chat_isolation():
    win = MessageWindow(size=10)
    win.add("a", "A1", ts=1.0)
    win.add("b", "B1", ts=2.0)
    assert win.count("a") == 1
    assert win.count("b") == 1
    assert sorted(win.chat_keys()) == ["a", "b"]
    win.clear("a")
    assert win.chat_keys() == ["b"]
    win.clear()
    assert win.chat_keys() == []


def test_recent_limit_and_default_ts():
    win = MessageWindow(size=10)
    entry = win.add("a", "hello", sender_id="u1", sender_name="小明")
    assert entry["ts"] > 0  # 缺省 ts 取当前时间
    assert [i["text"] for i in win.recent("a", limit=1)] == ["hello"]
    assert win.recent("a", limit=0) == []


def test_silence_gap_and_time_span():
    win = MessageWindow(size=10)
    assert win.silence_gap("a") == -1.0  # 空
    win.add("a", "m1", ts=1000.0)
    assert win.silence_gap("a") == -1.0  # 只有一条，间隔未知
    win.add("a", "m2", ts=1000.0 + 2400.0)  # 40 分钟后
    assert win.silence_gap("a") == 2400.0
    assert win.time_span("a") == 2400.0
    assert win.last_ts("a") == 3400.0


def test_snapshot_text_format():
    win = MessageWindow(size=10)
    win.add("a", "第一条", sender_id="u1", sender_name="小明", ts=1.0)
    win.add("a", "", sender_id="u2", sender_name="空消息被跳过", ts=2.0)
    win.add("a", "第二条", sender_id="u2", ts=3.0)
    text = win.snapshot_text("a", limit=10)
    assert "小明(u1): 第一条" in text
    assert "(u2): 第二条" in text
    assert "空消息被跳过" not in text


def test_group_key_helpers():
    assert is_group_key("onebot11:group:123")
    assert not is_group_key("onebot11:private:u1")
    assert group_keys(["a:group:1", "b:private:2", "c:group:3"]) == ["a:group:1", "c:group:3"]


def test_thread_safety_smoke():
    win = MessageWindow(size=1000)
    errors = []

    def worker(n):
        try:
            for i in range(200):
                win.add("shared", f"w{n}-{i}", ts=float(i))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert win.count("shared") == 800
