"""M7 · Agent 间通信 —— mailbox(收件箱队列)。

对标 Claude Code: src/utils/mailbox.ts(全文才 73 行)
讲清的原理: 通信机制本身极简(一个队列),复杂度都在"谁能给谁发"的上层。

────────────────────────────────────────────────────────────────────────
TODO(M7): 一个极简异步消息队列
  - send(msg):     有人正在等(waiter 匹配)就直接唤醒,否则入队
  - receive(filter): 取一条匹配的;没有就挂起等待(返回可 await 对象)
  - poll(filter):  非阻塞瞅一眼有没有
配套(可放 tool 模块): send_message 工具 —— Agent 间唯一的通信方式。
强约束: Agent 的普通输出别人看不见,要沟通必须调这个工具。
对比 M6: Coordinator 是星型(单向回灌),Team 是网状(双向通信)。
对标讲解见 docs/agent-orchestration.md 第 4.5-C 节。
────────────────────────────────────────────────────────────────────────
"""

# TODO(M7): 删除这行,开始实现 Mailbox。
raise NotImplementedError("M7: 实现 mailbox(见本文件顶部 TODO)")
