# Agent Orchestra

一个**学习导向**的多 Agent 编排引擎 —— 用 Python 从零复刻 **Claude Code 的真实编排设计**。

> 目标不是"又一个框架",而是:**每个模块独立可跑、可读,讲清一个编排原理**。
> 跟着 [`specs/07-迭代开发计划.md`](./specs/07-迭代开发计划.md) 一个迭代一个迭代实现,做到最后你将拥有一个
> 结构对标 Claude Code 的多 Agent 编排内核,并落地到一个真实业务场景
> (面向海外买家的中国供应商挖掘与画像)。

## 快速开始

```bash
make install     # uv sync,装依赖
make run         # 看当前迭代进度 + 下一步该做什么
make test        # 跑测试(骨架自带的冒烟测试应当全绿)
make check       # 提交前:格式化 + lint + 测试
```

不用 make 也行,直接:`uv run orchestra` / `uv run pytest` / `uv run ruff check .`

## 你要做什么 vs 已经给你铺好的

| 已铺好(工程地基,不用碰) | 你来写(编排逻辑,学习核心) |
|---|---|
| uv 包配置、依赖分组 | `message.py` `model.py`(M1) |
| ruff(lint+格式化,含 async 专项) | `tool.py` `context.py`(M2) |
| pytest(async 自动模式) | `orchestration.py`(M3 ⭐) |
| CLI 入口 / Makefile | `loop.py`(M4) |
| 各模块占位 + TODO + 对标注释 | `subagent.py` `coordinator.py` `mailbox.py`(M5–M7) |

每个待实现模块顶部都有:**对标 Claude Code 哪个文件 + 该写什么 + 验证标准**。
打开它,删掉 `raise NotImplementedError`,按 TODO 写即可。

## 结构

```
src/orchestra/
  message.py        M1  消息抽象(对话的原子)
  model.py          M1  模型抽象 + MockModel(无需 API key 即可驱动编排)
  tool.py           M2  工具系统(is_concurrency_safe 自声明)
  context.py        M2  运行上下文(abort + 递归深度)
  orchestration.py  M3  ⭐ 读并发 / 写独占 + 流式
  loop.py           M4  主循环(ReAct:推理→行动→观察 + maxTurns)
  subagent.py       M5  子 Agent(递归 + 上下文隔离)
  coordinator.py    M6  协调器(Orchestrator-Workers)
  mailbox.py        M7  Agent 间通信
  cli.py            —   进度看板入口
tests/              每个迭代配套测试
examples/           每个迭代一个最小 demo(对应五大模式)
specs/              ★ 设计资料包(需求/架构/规格/迭代计划)
```

## 文档导航

所有设计文档集中在 **`specs/`** 文件夹(拿到它就能开发)。从 `specs/00-README-导读.md` 开始读:

| 文档 | 讲什么 | 什么时候看 |
|---|---|---|
| [`specs/00-README-导读.md`](./specs/00-README-导读.md) | 资料包总入口、阅读顺序 | 最先看 |
| [`specs/01-功能需求清单.md`](./specs/01-功能需求清单.md) ★ | **要实现的 24 条功能;做完=几乎完全复刻** | 核心,先读 |
| [`specs/04-参考-CC编排原理.md`](./specs/04-参考-CC编排原理.md) | Claude Code 真实编排原理(我们在复刻什么) | 想懂原理 |
| [`specs/05-技术架构设计.md`](./specs/05-技术架构设计.md) | 整体架构:分层、模块咬合、数据流 | 想懂"为什么这么设计" |
| [`specs/06-多agent编排规格.md`](./specs/06-多agent编排规格.md) | 多 Agent 编排实现规格(递归/星型/网状) | 实现协作层前 |
| [`specs/07-迭代开发计划.md`](./specs/07-迭代开发计划.md) | M0–M11 迭代拆解(按什么顺序做) | 开始动手前 |

## 理论参考

对标讲解见 Claude Code 仓库的 `docs/agent-orchestration.md`(技术版)
与 `docs/agent-orchestration-图解版.md`(产品视角)。
