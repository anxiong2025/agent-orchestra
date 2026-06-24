"""M6 · 协调器 —— Orchestrator-Workers(教科书式编排-执行)。

对标 Claude Code: src/coordinator/coordinatorMode.ts(§4.5-A)
讲清的原理: 编排权集中在 leader,worker 只干活;异步结果伪装成"用户消息"回灌。

────────────────────────────────────────────────────────────────────────
TODO(M6):
  - coordinator 模式开关
  - leader 的 system prompt: "你只派活+综合,不自己下场写"
  - worker 工具收窄(白名单): 不给 worker 再派子 Agent 的权力
    (对标 INTERNAL_WORKER_TOOLS 被移除)
  - worker 结果包装成"伪用户消息"回灌 leader(对标 <task-notification> §4.5-B)
    → leader 当成"又收到一条用户消息"处理,无缝融进主循环
  - 脚本化场景: leader 派 2 worker 并行研究 → 收两份结论 → 综合
对标讲解见 docs/agent-orchestration.md 第 4.5 节。
────────────────────────────────────────────────────────────────────────
"""

# TODO(M6): 删除这行,开始实现协调器模式。
raise NotImplementedError("M6: 实现协调器(见本文件顶部 TODO)")
