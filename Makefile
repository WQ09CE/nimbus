# Nimbus Makefile
# 
# Usage:
#   make start      # 启动所有服务
#   make stop       # 停止所有服务
#   make status     # 查看状态
#   make dev        # 开发模式 (前台)
#

.PHONY: start stop restart status logs dev docker-dev-restart install test clean help test-e2e test-e2e-integration test-e2e-all

# 默认目标
help:
	@echo ""
	@echo "Nimbus - AI Agent Framework"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  start      Start all services (background)"
	@echo "  stop       Stop all services"
	@echo "  restart    Restart all services"
	@echo "  status     Show service status"
	@echo "  logs       View all logs"
	@echo "  dev        Start in foreground (dev mode)"
	@echo "  docker-dev-restart"
	@echo "             Restart Docker app with local backend src mounted"
	@echo "  install    Install dependencies"
	@echo "  test       Run tests"
	@echo "  clean      Clean up logs and cache"
	@echo ""

# 启动所有服务
start:
	@./nimbus start

# 停止所有服务
stop:
	@./nimbus stop

# 重启
restart:
	@./nimbus restart

# 状态
status:
	@./nimbus status

# 日志
logs:
	@./nimbus logs

# 开发模式 - 只启动 pi-ai 和 nimbus，前台运行 nimbus
dev:
	@echo "Starting pi-ai in background..."
	@./scripts/start-pi-ai.sh --daemon
	@echo ""
	@echo "Starting nimbus in foreground..."
	@echo "Press Ctrl+C to stop"
	@uv run nimbus serve

# Docker dev restart: reuse the existing image, but mount local Python source.
docker-dev-restart:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml restart nimbus

# 安装依赖
install:
	@echo "Installing Python dependencies..."
	pip install -e ".[all]"
	@echo ""
	@echo "Installing Node.js dependencies..."
	npm install @mariozechner/pi-ai
	cd web-ui && npm install
	@echo ""
	@echo "Done!"

# 运行测试
test:
	pytest tests/ -v

# 清理
clean:
	rm -rf .logs/
	rm -rf __pycache__/
	rm -rf .pytest_cache/
	find . -name "*.pyc" -delete
	@echo "Cleaned up!"

# E2E 测试 (tier1 - 快速冒烟测试)
test-e2e:
	cd web-ui && npx playwright test --project=tier1

# E2E 集成测试 (tier2 - 完整集成测试)
test-e2e-integration:
	cd web-ui && npx playwright test --project=tier2

# E2E 全部测试
test-e2e-all:
	cd web-ui && npx playwright test

# 只启动后端 (不启动 web-ui)
start-backend:
	@./nimbus start --no-ui
