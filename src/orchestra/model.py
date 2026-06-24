"""M1 · 模型抽象 + MockModel —— 让"调模型"可替换、可离线。

对标 Claude Code: src/query/deps.ts(依赖注入,模型可替换)
讲清的原理: 把"调 LLM"做成可注入依赖 → 整个编排无需真实 API 即可测试/演示。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from orchestra.message import Message


class Model(ABC):
    @abstractmethod
    async def complete(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> Message:
        """接收对话历史(可选工具列表),返回一条 assistant 消息。接口 provider 无关。

        tools: 每个工具的 schema(name/description/input_schema)。provider 负责
               翻译成各家 API 的格式,并把模型的"要调工具"翻译回我们的 ToolCall。
        """


class MockModel(Model):
    """按预设脚本顺序吐回应,无需 API key,驱动前 8 个迭代的测试与演示。

    脚本里的 Message 可以带 tool_calls —— 用来模拟"模型决定调工具"。
    """

    def __init__(self, script: list[Message]) -> None:
        self._script = list(script)
        self._index = 0

    async def complete(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> Message:
        if self._index >= len(self._script):
            # 脚本耗尽后返回空文本,让 loop 自然结束
            return Message.assistant(content="(done)")
        reply = self._script[self._index]
        self._index += 1
        return reply
