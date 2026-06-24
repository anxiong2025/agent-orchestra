"""provider 子包 —— 每家模型一个类,都实现 M1 的同一个 `Model` 抽象。

讲清的原理: 编排内核只认 `Model.complete()`,provider 内部负责把统一的
Message/ToolCall <-> 各家 API 格式互转。换模型 = 换一个 provider 类,编排逻辑零改动。

M1 只做 Bedrock(走你本地的 AWS 凭证接 Claude)。M9 再扩成多家 + 配置切换。
"""

from __future__ import annotations

from orchestra.model import Model


def make_model(provider: str = "bedrock", model_name: str | None = None) -> Model:
    """工厂:按名字造一个 provider。M9 会在这里接入更多家。"""
    if provider == "bedrock":
        from orchestra.providers.bedrock import BedrockClaudeModel

        return BedrockClaudeModel(model_name=model_name)
    raise ValueError(f"未知 provider: {provider}(M1 只支持 'bedrock')")
