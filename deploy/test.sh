#!/bin/bash
#
# Nimbus 部署测试脚本
#
# Usage: ./deploy/test.sh [local|remote]
#

set -e

MODE=${1:-local}

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[✓]${NC} $1"; }
log_fail() { echo -e "${RED}[✗]${NC} $1"; }

# ============================================================================
# 测试函数
# ============================================================================

test_port() {
    local port=$1
    local name=$2
    
    if lsof -i :$port -sTCP:LISTEN >/dev/null 2>&1; then
        log_ok "$name running on :$port"
        return 0
    else
        log_fail "$name not running on :$port"
        return 1
    fi
}

test_http() {
    local url=$1
    local name=$2
    
    if curl -sf "$url" >/dev/null 2>&1; then
        log_ok "$name accessible: $url"
        return 0
    else
        log_fail "$name not accessible: $url"
        return 1
    fi
}

test_health() {
    local url=$1
    
    local response=$(curl -sf "$url")
    if echo "$response" | grep -q "ok\|healthy\|running"; then
        log_ok "Health check passed: $url"
        return 0
    else
        log_fail "Health check failed: $url"
        return 1
    fi
}

# ============================================================================
# 本地测试
# ============================================================================

test_local() {
    echo ""
    echo "🧪 Testing Local Deployment"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    local passed=0
    local failed=0
    
    # 测试端口
    log_info "Testing ports..."
    test_port 3031 "pi-ai" && ((passed++)) || ((failed++))
    test_port 4096 "nimbus" && ((passed++)) || ((failed++))
    test_port 3000 "web-ui" && ((passed++)) || ((failed++))
    
    echo ""
    
    # 测试 HTTP
    log_info "Testing HTTP endpoints..."
    test_http "http://localhost:4096/health" "Nimbus API" && ((passed++)) || ((failed++))
    test_http "http://localhost:3000" "Web UI" && ((passed++)) || ((failed++))
    
    echo ""
    
    # 测试健康检查
    log_info "Testing health endpoints..."
    test_health "http://localhost:4096/health" && ((passed++)) || ((failed++))
    
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${GREEN}Passed: $passed${NC} | ${RED}Failed: $failed${NC}"
    
    if [ $failed -eq 0 ]; then
        echo -e "${GREEN}✅ All local tests passed!${NC}"
        return 0
    else
        echo -e "${RED}❌ Some tests failed${NC}"
        return 1
    fi
}

# ============================================================================
# 远程测试
# ============================================================================

test_remote() {
    echo ""
    echo "🌐 Testing Remote Deployment"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    local passed=0
    local failed=0
    local domain="erqing.wang"
    
    # 测试 Nginx 反向代理
    log_info "Testing Nginx reverse proxy..."
    test_http "http://$domain" "Web UI (Nginx)" && ((passed++)) || ((failed++))
    test_http "http://$domain/api/health" "Nimbus API (Nginx)" && ((passed++)) || ((failed++))
    
    echo ""
    
    # 测试健康检查
    log_info "Testing remote health endpoints..."
    test_health "http://$domain/api/health" && ((passed++)) || ((failed++))
    
    echo ""
    
    # 测试 SSE 支持
    log_info "Testing SSE support..."
    if timeout 5 curl -sf -N "http://$domain/api/v2/sessions/test/events" 2>&1 | head -1 | grep -q "event\|data"; then
        log_ok "SSE endpoint accessible"
        ((passed++))
    else
        log_fail "SSE endpoint not accessible"
        ((failed++))
    fi
    
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${GREEN}Passed: $passed${NC} | ${RED}Failed: $failed${NC}"
    
    if [ $failed -eq 0 ]; then
        echo -e "${GREEN}✅ All remote tests passed!${NC}"
        echo ""
        echo "🎉 Your Nimbus is now accessible at:"
        echo "   http://$domain"
        return 0
    else
        echo -e "${RED}❌ Some tests failed${NC}"
        echo ""
        echo "💡 Troubleshooting tips:"
        echo "   1. Check if services are running: ./nimbus status"
        echo "   2. Check Nginx configuration: sudo nginx -t"
        echo "   3. Check logs: ./nimbus logs"
        return 1
    fi
}

# ============================================================================
# 主流程
# ============================================================================

main() {
    case "$MODE" in
        local)
            test_local
            ;;
        remote)
            test_remote
            ;;
        all)
            test_local
            echo ""
            test_remote
            ;;
        *)
            echo "Usage: $0 [local|remote|all]"
            exit 1
            ;;
    esac
}

main "$@"
