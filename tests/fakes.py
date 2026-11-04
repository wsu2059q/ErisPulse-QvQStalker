"""最小测试替身（参照 QvQChat/tests/fakes.py 风格，独立精简、零 QvQChat 依赖）。"""

from __future__ import annotations

import asyncio


class FakeStorage:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def delete(self, key):
        self._data.pop(key, None)

    def keys(self):
        return list(self._data)


class FakeLogger:
    def __init__(self):
        self.records = []  # (level, message)

    def _log(self, level, *args, **kwargs):
        self.records.append((level, " ".join(str(a) for a in args)))

    def debug(self, *args, **kwargs):
        self._log("debug", *args)

    def info(self, *args, **kwargs):
        self._log("info", *args)

    def warning(self, *args, **kwargs):
        self._log("warning", *args)

    def error(self, *args, **kwargs):
        self._log("error", *args)

    def get_child(self, name):
        child = FakeLogger()
        self.children = getattr(self, "children", {})
        self.children[name] = child
        # 让子 logger 的记录同时冒泡到父，便于断言
        child.records = self.records
        return child

    def of_level(self, level):
        return [msg for lv, msg in self.records if lv == level]


class FakeLifecycle:
    def __init__(self):
        self.registered = []   # (event, handler)
        self.unregistered = []  # (event, handler)
        self.emitted = []

    def register(self, event, handler, *_, **__):
        self.registered.append((event, handler))

    def unregister(self, event, handler=None, *_, **__):
        self.unregistered.append((event, handler))

    async def emit(self, event, data=None, **kwargs):
        self.emitted.append((event, data))
        results = []
        for evt, handler in list(self.registered):
            if evt != event:
                continue
            result = handler(data)
            if asyncio.iscoroutine(result):
                result = await result
            results.append(result)
        return data


class FakeModuleManager:
    """module.call 替身：handlers[(module, service)] = callable(**kwargs) | 常量。"""

    def __init__(self, handlers=None):
        self.handlers = dict(handlers or {})
        self.calls = []  # (module, service, kwargs)

    async def call(self, module, service, *args, **kwargs):
        self.calls.append((module, service, kwargs))
        key = (module, service)
        if key in self.handlers:
            handler = self.handlers[key]
            if callable(handler):
                result = handler(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            return handler
        raise RuntimeError(f"module {module} service {service} not available")


class FakeSDK:
    def __init__(self, handlers=None):
        self.storage = FakeStorage()
        self.logger = FakeLogger()
        self.lifecycle = FakeLifecycle()
        self.module = FakeModuleManager(handlers)


def make_config(**overrides):
    from QvQStalker.Config import QvQStalkerConfig

    fields = QvQStalkerConfig.__dataclass_fields__
    bad = [k for k in overrides if k not in fields or k.startswith("_")]
    if bad:
        raise TypeError(f"unknown config keys: {bad}")
    return QvQStalkerConfig(**overrides)
