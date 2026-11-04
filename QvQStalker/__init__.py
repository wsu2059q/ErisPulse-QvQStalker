"""QvQStalker：ErisPulse 群聊参与决策插件（QvQChat 伴侣模块）。

旁听群消息（qvq.observe）、五层插话判定（qvq_decision 约定服务）、
冲动值主动发言（QvQChat.speak 服务）。零 QvQChat 包依赖，
仅经 sdk.lifecycle / sdk.module.call / sdk.storage 交互。
"""

from .Main import Main, __version__

__all__ = ["Main", "__version__"]
