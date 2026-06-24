"""M1 · 模型抽象 + MockModel —— 让"调模型"可替换、可离线。

对标 Claude Code: src/query/deps.ts(依赖注入,模型可替换)
讲清的原理: 把"调 LLM"做成可注入依赖 → 整个编排无需真实 API 即可测试/演示。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orchestra.message import Message


class Model(ABC):
    @abstractmethod
    async def complete(self, messages: list[Message]) -> Message:
        """接收对话历史,返回一条 assistant 消息。接口 provider 无关。"""


class MockModel(Model):
    """按预设脚本顺序吐回应,无需 API key,驱动前 8 个迭代的测试与演示。"""

    def __init__(self, script: list[Message]) -> None:
        self._script = list(script)
        self._index = 0

    async def complete(self, messages: list[Message]) -> Message:
        if self._index >= len(self._script):
            # 脚本耗尽后返回空文本,让 loop 自然结束
            return Message.assistant(content="(done)")
        reply = self._script[self._index]
        self._index += 1
        return reply
