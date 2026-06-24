"""M1 · 消息抽象 —— 对话的"原子"。

对标 Claude Code: src/types/message.ts
讲清的原理: 上下文就是一个 Message 列表;主循环每一轮往里追加。

────────────────────────────────────────────────────────────────────────
TODO(M1): 在这里定义对话的数据结构。建议(不强制):
  - Role: user / assistant / system
  - Message: role + content
  - 助手消息要能携带"工具调用"列表(name + input + 一个 id)
  - 工具结果也要能作为一条消息回灌(带上对应的工具调用 id)
提示: 用 dataclass 或 pydantic 都行;先简单,后面不够再加。
对标讲解见 docs/agent-orchestration.md 第 1 节。
────────────────────────────────────────────────────────────────────────
"""

# TODO(M1): 删除这行,开始定义 Message / Role / ToolCall。
raise NotImplementedError("M1: 实现消息抽象(见本文件顶部 TODO)")
