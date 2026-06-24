# 工程常用命令。`make` 看帮助。全部通过 uv 运行,无需手动激活 venv。
.DEFAULT_GOAL := help
.PHONY: help install run test fmt lint check

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n",$$1,$$2}'

install: ## 安装依赖(含 dev)
	uv sync

run: ## 运行 CLI(看当前迭代进度)
	uv run orchestra

test: ## 跑测试
	uv run pytest

fmt: ## 格式化代码
	uv run ruff format .

lint: ## 静态检查(含 async 专项)
	uv run ruff check .

check: fmt lint test ## 提交前一把梭:格式化 + lint + 测试
