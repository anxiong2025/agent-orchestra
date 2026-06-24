"""Bedrock Claude provider —— 走 AWS Bedrock 接真实 Claude。

对标 Claude Code: query/deps.ts 的可注入模型依赖。这里是它的"真实"实现,
和 MockModel 并列 —— 测试用 Mock,体验用真模型,编排内核两边通吃。

凭证: 直接复用你本地的 AWS 环境变量(AWS_BEARER_TOKEN_BEDROCK / AWS_REGION),
SDK 自动读取,代码里不碰 key。

M3 起这一层还负责【工具调用的双向翻译】:
  - 出:把我们统一的 tools schema + tool_result 消息 → Anthropic API 格式
  - 入:把 Claude 回复里的 tool_use block → 我们统一的 ToolCall
内核(loop/tool)完全不感知 Anthropic 的字段长什么样。
"""

from __future__ import annotations

import os
from typing import Any

from anthropic import AsyncAnthropicBedrock

from orchestra.message import Message, Role, ToolCall
from orchestra.model import Model

# Bedrock 推理档位 ID(带 us. 前缀)。默认用 env 里配好的 Sonnet,够聊天用。
_DEFAULT_MODEL = os.environ.get(
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6"
)


class BedrockClaudeModel(Model):
    """把统一的 Message 列表翻译成 Bedrock API 调用,再把回复翻译回 Message。"""

    def __init__(self, model_name: str | None = None, max_tokens: int = 4096) -> None:
        self._model = model_name or _DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._client = AsyncAnthropicBedrock(
            aws_region=os.environ.get("AWS_REGION", "us-east-1")
        )

    async def complete(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> Message:
        system, api_messages = self._to_api(messages)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": api_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            # 我们的 {name, description, input_schema} → Anthropic 的 tool 定义。
            kwargs["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema", {"type": "object"}),
                }
                for t in tools
            ]

        resp = await self._client.messages.create(**kwargs)

        # 入向翻译:Claude 的 content block → 我们的 Message(文本 + ToolCall)。
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input))
                )
        return Message.assistant(content="".join(text_parts), tool_calls=tool_calls)

    @staticmethod
    def _to_api(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """统一 Message → Anthropic 格式。

        - system 单独抽出
        - assistant 带工具调用 → content 里放 text + tool_use block
        - 工具结果(user + tool_call_id)→ content 里放 tool_result block
        - 普通消息 → 纯文本 content
        """
        system_parts: list[str] = []
        api_messages: list[dict[str, Any]] = []

        for m in messages:
            if m.role is Role.system:
                system_parts.append(m.content)

            elif m.role is Role.assistant and m.tool_calls:
                content: list[dict[str, Any]] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.input,
                        }
                    )
                api_messages.append({"role": "assistant", "content": content})

            elif m.role is Role.user and m.tool_call_id:
                # 工具结果回灌:必须是 user 消息里的 tool_result block。
                api_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )

            else:
                api_messages.append({"role": m.role.value, "content": m.content})

        return "\n\n".join(system_parts), api_messages
