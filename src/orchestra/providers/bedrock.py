"""Bedrock Claude provider —— 走 AWS Bedrock 接真实 Claude。

对标 Claude Code: query/deps.ts 的可注入模型依赖。这里是它的"真实"实现,
和 MockModel 并列 —— 测试用 Mock,体验用真模型,编排内核两边通吃。

凭证: 直接复用你本地的 AWS 环境变量(AWS_BEARER_TOKEN_BEDROCK / AWS_REGION),
SDK 自动读取,代码里不碰 key。
"""

from __future__ import annotations

import os

from anthropic import AsyncAnthropicBedrock

from orchestra.message import Message, Role
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

    async def complete(self, messages: list[Message]) -> Message:
        system, api_messages = self._to_api(messages)
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": api_messages,
        }
        if system:
            kwargs["system"] = system

        resp = await self._client.messages.create(**kwargs)

        # M1 只取文本回复(工具调用留到 M3 工具系统接进来时再翻译)。
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Message.assistant(content=text)

    @staticmethod
    def _to_api(messages: list[Message]) -> tuple[str, list[dict]]:
        """统一 Message -> Bedrock 格式。system 单独抽出,其余按 user/assistant 排列。"""
        system_parts: list[str] = []
        api_messages: list[dict] = []
        for m in messages:
            if m.role is Role.system:
                system_parts.append(m.content)
            else:
                api_messages.append({"role": m.role.value, "content": m.content})
        return "\n\n".join(system_parts), api_messages
