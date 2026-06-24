"""M1 · 模型抽象 + MockModel —— 让"调模型"可替换、可离线。

对标 Claude Code: src/query/deps.ts(依赖注入,模型可替换)
讲清的原理: 把"调 LLM"做成可注入依赖 → 整个编排无需真实 API 即可测试/演示。

────────────────────────────────────────────────────────────────────────
TODO(M1):
  - Model(抽象基类): `async def complete(self, messages) -> AssistantMessage`
    ⚠️ 这个接口要"provider 无关"—— 它将来要同时被 Claude/GPT/国内模型实现。
       所以接口里只用我们自己的 Message/ToolCall 类型,绝不泄漏任何某家 API 的字段。
  - MockModel(Model): 构造时传入一个"脚本"(预设的助手回应序列),
    每次 complete 按顺序吐一条。这是前 8 个迭代的驱动力(无需 API key)。

TODO(M9): 加 providers/ 子包,每家一个类都实现这个 Model 抽象,可灵活切换:
  - BedrockClaudeModel(开发阶段先做,走 AWS Bedrock 接 Claude)
  - AnthropicModel / OpenAIModel / 国内模型(通义/DeepSeek/智谱…)
  - make_model(provider, model_name) 工厂 + 配置选择
  各 provider 内部负责把统一 Message/ToolCall <-> 各家 API 格式互转。
  详见 ROADMAP.md 的 M9。
────────────────────────────────────────────────────────────────────────
"""

# TODO(M1): 删除这行,开始定义 Model / MockModel。
raise NotImplementedError("M1: 实现模型抽象与 MockModel(见本文件顶部 TODO)")
