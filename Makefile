# Nimbus Makefile
# 
# Usage:
#   make start      # 启动所有服务
#   make stop       # 停止所有服务
#   make status     # 查看状态
#   make dev        # 开发模式 (前台)
#

.PHONY: start stop restart status logs dev install test clean help

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

# 只启动后端 (不启动 web-ui)
start-backend:
	@./nimbus start --no-ui
