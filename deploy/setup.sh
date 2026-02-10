#!/bin/bash
#
# Nimbus 一键部署脚本
#
# Usage: ./deploy/setup.sh [production|development]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV=${1:-development}

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================================================
# 检测操作系统
# ============================================================================

detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

OS=$(detect_os)

# ============================================================================
# 检查依赖
# ============================================================================

check_dependencies() {
    log_info "Checking dependencies..."
    
    local missing=()
    
    # 必需工具
    for cmd in node npm npx uv git; do
        if ! command -v $cmd &> /dev/null; then
            missing+=("$cmd")
        fi
    done
    
    # Nginx
    if ! command -v nginx &> /dev/null; then
        log_warn "nginx not found"
        if [[ "$OS" == "macos" ]]; then
            log_info "Install with: brew install nginx"
        else
            log_info "Install with: sudo apt install nginx"
        fi
        missing+=("nginx")
    fi
    
    if [ ${#missing[@]} -gt 0 ]; then
        log_error "Missing dependencies: ${missing[*]}"
        exit 1
    fi
    
    log_ok "All dependencies installed"
}

# ============================================================================
# 配置环境
# ============================================================================

setup_env() {
    log_info "Setting up environment..."
    
    cd "$PROJECT_DIR"
    
    # 创建 .env 如果不存在
    if [ ! -f .env ]; then
        log_info "Creating .env file..."
        cat > .env << 'EOF'
# Discord Bot
DISCORD_BOT_TOKEN=
BOT_PREFIX=!nimbus

# Nimbus API
NIMBUS_API_URL=http://localhost:4096

# Ports
PI_AI_PORT=3031
NIMBUS_PORT=4096
WEBUI_PORT=3000
EOF
        log_warn ".env created. Please edit it and add your DISCORD_BOT_TOKEN"
    fi
    
    # 安装依赖
    log_info "Installing bridge dependencies..."
    cd bridge
    npm install discord.js node-fetch --silent 2>/dev/null || true
    cd ..
    
    log_info "Installing web-ui dependencies..."
    cd web-ui
    npm install --silent 2>/dev/null || true
    cd ..
    
    log_ok "Environment setup complete"
}

# ============================================================================
# 配置服务自启动
# ============================================================================

setup_service() {
    log_info "Configuring service autostart..."
    
    if [[ "$OS" == "macos" ]]; then
        # macOS launchd
        local plist="/Library/LaunchDaemons/com.nimbus.server.plist"
        
        if [ -f "$plist" ]; then
            log_warn "Service already exists. Unloading..."
            sudo launchctl unload "$plist" 2>/dev/null || true
        fi
        
        sudo cp "$SCRIPT_DIR/com.nimbus.server.plist" "$plist"
        sudo launchctl load "$plist"
        
        log_ok "macOS service configured"
        
    elif [[ "$OS" == "linux" ]]; then
        # Linux systemd
        sudo cp "$SCRIPT_DIR/nimbus.service" /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable nimbus.service
        
        log_ok "Linux service configured"
    else
        log_error "Unsupported OS: $OS"
        exit 1
    fi
}

# ============================================================================
# 配置 Nginx
# ============================================================================

setup_nginx() {
    log_info "Configuring Nginx..."
    
    if [[ "$OS" == "macos" ]]; then
        local nginx_conf="/usr/local/etc/nginx/servers/nimbus.conf"
        sudo cp "$SCRIPT_DIR/nginx.conf" "$nginx_conf"
    else
        local nginx_conf="/etc/nginx/sites-available/nimbus"
        sudo cp "$SCRIPT_DIR/nginx.conf" "$nginx_conf"
        sudo ln -sf "$nginx_conf" /etc/nginx/sites-enabled/
    fi
    
    # 测试配置
    if sudo nginx -t &> /dev/null; then
        log_ok "Nginx configuration valid"
        sudo nginx -s reload 2>/dev/null || sudo systemctl reload nginx
    else
        log_error "Nginx configuration invalid"
        exit 1
    fi
}

# ============================================================================
# 配置 Web UI 环境
# ============================================================================

setup_webui_env() {
    log_info "Configuring Web UI..."
    
    local env_file="$PROJECT_DIR/web-ui/.env.local"
    
    if [[ "$ENV" == "production" ]]; then
        cat > "$env_file" << 'EOF'
NEXT_PUBLIC_API_BASE_URL=http://erqing.wang/api
EOF
        log_ok "Web UI configured for production (erqing.wang)"
    else
        cat > "$env_file" << 'EOF'
NEXT_PUBLIC_API_BASE_URL=http://localhost:4096
EOF
        log_ok "Web UI configured for development (localhost)"
    fi
}

# ============================================================================
# 启动服务
# ============================================================================

start_services() {
    log_info "Starting services..."
    
    if [[ "$OS" == "macos" ]]; then
        sudo launchctl start com.nimbus.server
    else
        sudo systemctl start nimbus
    fi
    
    sleep 3
    
    # 检查服务状态
    cd "$PROJECT_DIR"
    ./nimbus status
}

# ============================================================================
# 显示后续步骤
# ============================================================================

show_next_steps() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${GREEN}✅ Nimbus Deployment Complete!${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "🌐 Web UI:"
    if [[ "$ENV" == "production" ]]; then
        echo "   http://erqing.wang"
    else
        echo "   http://localhost:3000"
    fi
    echo ""
    echo "🔧 API:"
    if [[ "$ENV" == "production" ]]; then
        echo "   http://erqing.wang/api"
    else
        echo "   http://localhost:4096"
    fi
    echo ""
    echo "🤖 Discord Bot:"
    echo "   1. Edit .env and add DISCORD_BOT_TOKEN"
    echo "   2. Run: screen -dmS nimbus-discord bash -c 'cd bridge && npx tsx discord-bot.ts >> ../.logs/discord-bot.log 2>&1'"
    echo "   3. Test in Discord: !nimbus hello"
    echo ""
    echo "📊 Service Management:"
    echo "   ./nimbus status         # Check status"
    echo "   ./nimbus logs           # View logs"
    echo "   ./nimbus restart        # Restart services"
    echo ""
    echo "📚 Documentation:"
    echo "   deploy/DEPLOY.md        # Full deployment guide"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ============================================================================
# 主流程
# ============================================================================

main() {
    echo ""
    echo "🚀 Nimbus Deployment Script"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Environment: $ENV"
    echo "OS: $OS"
    echo ""
    
    check_dependencies
    setup_env
    setup_webui_env
    
    if [[ "$ENV" == "production" ]]; then
        setup_service
        setup_nginx
    fi
    
    start_services
    show_next_steps
}

main "$@"
