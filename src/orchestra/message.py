"""M1 · 消息抽象 —— 对话的"原子"。

对标 Claude Code: src/types/message.ts
讲清的原理: 上下文就是一个 Message 列表;主循环每一轮往里追加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # 当 role=user 且这个字段非空时,表示这是一条工具结果回灌消息
    tool_call_id: str | None = None

    @staticmethod
    def system(content: str) -> Message:
        return Message(role=Role.system, content=content)

    @staticmethod
    def user(content: str) -> Message:
        return Message(role=Role.user, content=content)

    @staticmethod
    def tool_result(tool_call_id: str, content: str) -> Message:
        return Message(role=Role.user, content=content, tool_call_id=tool_call_id)

    @staticmethod
    def assistant(content: str = "", tool_calls: list[ToolCall] | None = None) -> Message:
        return Message(role=Role.assistant, content=content, tool_calls=tool_calls or [])
