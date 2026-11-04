"""插件设置（QvQChat 约定）：qvq_settings 归属标记形状/白名单、plugin_config 现值合并、热更新。"""

from QvQStalker.Main import PLUGIN_CONFIG_EVENT, Main

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


# ==================== qvq_settings：归属标记形状与白名单 ====================

def test_qvq_settings_in_services_whitelist():
    assert "qvq_settings" in meta_services()


async def test_qvq_settings_is_title_marker_only():
    """配置项由 ConfigClass 声明（框架 schema 流），服务只返回归属标记。"""
    main = make_main()
    data = await main.qvq_settings()
    assert data == {"title": "QvQStalker 群聊参与决策"}
    assert "items" not in data


# ==================== 现值合并（on_load 经 plugin_config 拉取） ====================

async def test_on_load_merges_plugin_config_overrides():
    sdk = FakeSDK(handlers={("QvQChat", "plugin_config"): lambda **kw: {
        "QvQStalker": {  # 新契约：{模块名: {k: v}} 嵌套形（getConfig 现值）
            "mode": "active",
            "urge_threshold": 2.5,
            "window_size": 30,
        },
        "unknown_key": "ignored",  # 模块名不在键过滤白名单
    }})
    main = make_main(sdk)
    assert await main.on_load({}) is True
    calls = [(c[0], c[1]) for c in sdk.module.calls]
    assert ("QvQChat", "plugin_config") in calls
    cfg = main._cfg()
    assert cfg.mode == "active"
    assert cfg.urge_threshold == 2.5
    assert cfg.window_size == 30
    await main.on_unload({})


async def test_plugin_config_flat_shape_still_applied():
    """兼容裸 {k: v}（防主体版本差异）。"""
    sdk = FakeSDK(handlers={("QvQChat", "plugin_config"): lambda **kw: {"mode": "conservative"}})
    main = make_main(sdk)
    assert await main.on_load({}) is True
    assert main._cfg().mode == "conservative"
    await main.on_unload({})


async def test_plugin_config_pull_failure_is_silent():
    main = make_main(FakeSDK())  # 无 handler → call 抛错 → 静默
    assert await main.on_load({}) is True
    assert main._cfg().mode == "balanced"  # 默认值原样
    await main.on_unload({})


# ==================== 热更新（订阅 qvq.plugin_config） ====================

async def test_plugin_config_event_hot_update():
    sdk = FakeSDK()
    main = make_main(sdk)
    await main.on_load({})
    assert (PLUGIN_CONFIG_EVENT, main._on_plugin_config) in sdk.lifecycle.registered
    # module 不匹配 → 忽略
    await sdk.lifecycle.emit(PLUGIN_CONFIG_EVENT, {"module": "Other", "values": {"mode": "active"}})
    assert main._cfg().mode == "balanced"
    # 匹配 → 重合并生效
    await sdk.lifecycle.emit(PLUGIN_CONFIG_EVENT, {
        "module": "QvQStalker",
        "values": {"mode": "conservative", "default_probability": 0.1},
    })
    assert main._cfg().mode == "conservative"
    assert main._cfg().default_probability == 0.1
    # 垃圾载荷不抛错
    for payload in (None, "str", 123, {}, {"module": "QvQStalker"}):
        await sdk.lifecycle.emit(PLUGIN_CONFIG_EVENT, payload)
    await main.on_unload({})
    assert (PLUGIN_CONFIG_EVENT, main._on_plugin_config) in sdk.lifecycle.unregistered
