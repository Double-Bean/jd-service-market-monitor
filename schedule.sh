#!/bin/bash
# 竞品监控 - 定时任务安装/卸载脚本
#
# 用法：
#   bash schedule.sh install    # 安装每天10:00的定时任务
#   bash schedule.sh uninstall  # 卸载定时任务
#   bash schedule.sh status     # 查看定时任务状态
#   bash schedule.sh run        # 立即执行一次

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_DIR="$SCRIPT_DIR"
LOG_DIR="$WORKSPACE_DIR/logs"
CRON_TAG="# COMPETITOR-MONITOR-DAILY"

mkdir -p "$LOG_DIR"

install() {
    # 检查是否已安装
    if crontab -l 2>/dev/null | grep -q "$CRON_TAG"; then
        echo "定时任务已存在，先卸载再重新安装..."
        uninstall
    fi

    # 添加定时任务（每天10:00执行）
    (crontab -l 2>/dev/null; echo "0 10 * * * cd $WORKSPACE_DIR && /usr/bin/python3 run.py 每日执行 >> $LOG_DIR/daily_monitor.log 2>&1 $CRON_TAG") | crontab -

    echo "✅ 定时任务已安装：每天 10:00 自动执行竞品监控"
    echo "   日志目录: $LOG_DIR/"
    echo ""
    echo "   提示：采集需要浏览器 CDP 端口 9222 可连接"
}

uninstall() {
    crontab -l 2>/dev/null | grep -v "$CRON_TAG" | crontab -
    echo "✅ 定时任务已卸载"
}

status() {
    if crontab -l 2>/dev/null | grep -q "$CRON_TAG"; then
        echo "定时任务状态: ✅ 已安装"
        crontab -l 2>/dev/null | grep "$CRON_TAG"
        echo ""
        echo "最近日志:"
        if [ -f "$LOG_DIR/daily_monitor.log" ]; then
            tail -10 "$LOG_DIR/daily_monitor.log"
        else
            echo "  暂无日志"
        fi
    else
        echo "定时任务状态: ❌ 未安装"
        echo ""
        echo "安装命令: bash $0 install"
    fi
}

run_now() {
    echo "立即执行每日监控..."
    cd "$WORKSPACE_DIR" && python3 run.py 每日执行
}

case "${1:-}" in
    install)   install ;;
    uninstall) uninstall ;;
    status)    status ;;
    run)       run_now ;;
    *)
        echo "用法: bash $0 {install|uninstall|status|run}"
        ;;
esac
