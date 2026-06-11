"""
竞品监控 Skill - 主执行脚本

功能：
  1. 采集单个/批量竞品链接
  2. 生成单个竞品 HTML 报告
  3. 生成每日竞品监控报告（所有竞品的当日数据总览）
  4. 生成每日竞品变化报告（对比前一天的价格/指标/活动变化）
  5. 一键执行每日监控（采集所有竞品 + 生成两份报告）

用法：
  python3 run.py 采集 "https://fw.jd.com/..." [分类]    # 采集单个竞品
  python3 run.py 批量采集                                 # 采集所有已入库的竞品
  python3 run.py 列表                                     # 查看已采集的竞品列表
  python3 run.py 报告 <商品名称>                          # 生成单个竞品报告
  python3 run.py 每日报告                                 # 生成每日监控报告 + 变化报告
  python3 run.py 每日执行                                 # 一键执行：采集所有竞品 + 生成报告
"""
import sys
import os
import re
import json
import logging
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.environ.get('COMPETITOR_MONITOR_OUTPUT_DIR', os.getcwd())
os.makedirs(OUTPUT_DIR, exist_ok=True)

from competitor_agent import CompetitorAgent

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def monitor(url: str, category: str = "") -> dict:
    """采集单个竞品链接"""
    agent = CompetitorAgent()
    try:
        result = agent.monitor(url, category=category)
        name = result.get('name', '未知')
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', name) if name else 'unknown'
        html = agent.generate_html_report(name)

        if html:
            report_path = os.path.join(OUTPUT_DIR, f'竞品报告_{safe_name}.html')
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(html)
            result['report_path'] = report_path

        return result
    finally:
        agent.close()


def monitor_batch() -> list:
    """采集所有已入库的竞品（批量更新）"""
    agent = CompetitorAgent()
    try:
        # 获取所有已入库的竞品 URL
        from competitor_agent import CompetitorRecord
        from sqlalchemy import distinct
        urls = [r[0] for r in agent.session.query(
            CompetitorRecord.url
        ).group_by(CompetitorRecord.url).all()]

        if not urls:
            logger.warning("数据库中暂无竞品，请先使用 monitor(url) 添加")
            return []

        logger.info(f"开始批量采集 {len(urls)} 个竞品")
        results = []
        for i, url in enumerate(urls, 1):
            try:
                result = agent.monitor(url)
                name = result.get('name', '未知')
                safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)
                html = agent.generate_html_report(name)
                if html:
                    report_path = os.path.join(OUTPUT_DIR, f'竞品报告_{safe_name}.html')
                    with open(report_path, 'w', encoding='utf-8') as f:
                        f.write(html)
                    result['report_path'] = report_path

                results.append(result)
                quality = result.get('quality_issue')
                attempts = result.get('attempts', 1)
                quality_note = f" | 尝试{attempts}次后仍异常:{quality}" if quality else f" | 尝试{attempts}次"
                logger.info(f"[{i}/{len(urls)}] ✅ {name} | 评分{result.get('score')} "
                            f"已买{result.get('buyers')} 续订率{result.get('renewal_rate')}%{quality_note}")
            except Exception as e:
                logger.error(f"[{i}/{len(urls)}] ❌ {url}: {e}")
                results.append({'url': url, 'error': str(e)})

        logger.info(f"批量采集完成: {len(results)} 个竞品")
        return results
    finally:
        agent.close()


def list_all() -> list:
    """列出所有已采集的竞品"""
    agent = CompetitorAgent()
    try:
        return agent.get_all_table()
    finally:
        agent.close()


def generate_report(name: str) -> str:
    """为已采集的竞品生成 HTML 报告"""
    agent = CompetitorAgent()
    try:
        html = agent.generate_html_report(name)
        if not html:
            return None
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)
        report_path = os.path.join(OUTPUT_DIR, f'竞品报告_{safe_name}.html')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return report_path
    finally:
        agent.close()


def generate_comparison_report(names: list) -> str:
    """生成多竞品对比 HTML 报告"""
    agent = CompetitorAgent()
    try:
        details = []
        for name in names:
            d = agent.get_detail(name)
            if d:
                details.append(d)

        if not details:
            return None

        rows = ''
        for d in details:
            price_display = d.get('price_display') or '-'
            rows += f'''<tr>
                <td><strong>{d['name']}</strong></td>
                <td>{d['score'] or '-'}</td>
                <td>{d['good_rate'] or 0}%</td>
                <td>{d['buyers'] or 0:,}+人</td>
                <td>{d['renewal_rate'] or '-'}%</td>
                <td>{price_display}</td>
                <td>{d['provider_name'] or '-'}</td>
            </tr>'''

        html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>竞品对比报告</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Microsoft YaHei',sans-serif; background:#f0f2f5; color:#333; padding:20px; }}
.container {{ max-width:1200px; margin:0 auto; }}
h1 {{ text-align:center; color:#1F4E79; margin-bottom:20px; font-size:24px; }}
.card {{ background:white; border-radius:12px; padding:24px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
.card h2 {{ color:#1F4E79; margin-bottom:16px; font-size:18px; border-bottom:2px solid #e8e8e8; padding-bottom:8px; }}
table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
th {{ background:#4472C4; color:white; padding:10px 14px; text-align:left; font-size:13px; }}
td {{ padding:10px 14px; border-bottom:1px solid #eee; font-size:13px; }}
tr:hover {{ background:#f8f9ff; }}
</style></head><body>
<div class="container">
<h1>🔍 竞品对比报告</h1>
<div class="card">
<h2>📊 核心指标对比</h2>
<table>
<tr><th>商品名称</th><th>评分</th><th>好评率</th><th>已买人数</th><th>续订率</th><th>价格区间</th><th>服务商</th></tr>
{rows}
</table>
</div>
</div>
</body></html>'''

        report_path = os.path.join(OUTPUT_DIR, '竞品对比报告.html')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return report_path
    finally:
        agent.close()


def generate_daily_reports() -> dict:
    """
    生成每日两份报告：
    1. 每日竞品监控报告 - 所有竞品的当日数据总览（含价格矩阵、指标、活动）
    2. 每日竞品变化报告 - 对比前一天的价格/指标/活动变化

    Returns:
        dict: {'monitor_report': path, 'change_report': path}
    """
    agent = CompetitorAgent()
    try:
        # 1. 每日竞品监控报告（含变化检测）
        monitor_html = agent.generate_daily_monitor_report()
        monitor_path = os.path.join(OUTPUT_DIR, '每日竞品变化监控.html')
        with open(monitor_path, 'w', encoding='utf-8') as f:
            f.write(monitor_html)
        logger.info(f"✅ 每日竞品变化监控报告: {monitor_path}")

        # 2. 为每个竞品生成单独的报告
        from competitor_agent import CompetitorRecord
        urls = [r[0] for r in agent.session.query(
            CompetitorRecord.url
        ).group_by(CompetitorRecord.url).all()]

        for url in urls:
            record = agent.session.query(CompetitorRecord).filter_by(
                url=url
            ).order_by(CompetitorRecord.recorded_at.desc()).first()
            if not record:
                continue
            name = record.name
            html = agent.generate_html_report(name)
            if html:
                safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)
                report_path = os.path.join(OUTPUT_DIR, f'竞品报告_{safe_name}.html')
                with open(report_path, 'w', encoding='utf-8') as f:
                    f.write(html)

        logger.info(f"✅ 各竞品单独报告: {len(urls)} 个")

        return {
            'monitor_report': monitor_path,
            'competitor_count': len(urls),
        }
    finally:
        agent.close()


def run_daily() -> dict:
    """
    一键执行每日监控完整流程：
    1. 批量采集所有竞品最新数据
    2. 生成每日竞品监控报告（含变化检测）

    Returns:
        dict: 执行结果
    """
    logger.info("=" * 50)
    logger.info(f"每日竞品监控开始 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info("=" * 50)

    # Step 1: 批量采集
    logger.info("[Step 1/2] 批量采集所有竞品...")
    results = monitor_batch()

    # Step 2: 生成报告
    logger.info("[Step 2/2] 生成每日报告...")
    report_info = generate_daily_reports()

    logger.info("=" * 50)
    logger.info(f"每日监控完成 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"  采集竞品: {len(results)} 个")
    logger.info(f"  监控报告: {report_info.get('monitor_report')}")
    logger.info("=" * 50)

    return {
        'collected': len(results),
        'results': results,
        'reports': report_info,
        'time': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]

    if arg == '列表':
        table = list_all()
        if not table:
            print("暂无已采集的竞品数据")
        else:
            print(f"共 {len(table)} 个竞品:\n")
            for item in table:
                print(f"  {item['名称']} | 评分:{item['评分']} | 已买:{item['已买人数']}+人 | 续订率:{item['续订率']} | 采集:{item['采集时间']}")

    elif arg == '批量采集':
        results = monitor_batch()
        print(f"\n批量采集完成: {len(results)} 个竞品")

    elif arg == '报告':
        name = sys.argv[2] if len(sys.argv) > 2 else ''
        path = generate_report(name)
        if path:
            print(f"报告已生成: {path}")
        else:
            print(f"未找到商品: {name}")

    elif arg == '每日报告':
        info = generate_daily_reports()
        print(f"监控报告: {info.get('monitor_report')}")
        print(f"竞品数量: {info.get('competitor_count')}")

    elif arg == '每日执行':
        result = run_daily()
        print(f"\n执行完成: {result['time']}")
        print(f"  采集: {result['collected']} 个竞品")
        print(f"  报告: {result['reports'].get('monitor_report')}")

    elif arg == '采集' or arg.startswith('http'):
        url = sys.argv[2] if arg == '采集' and len(sys.argv) > 2 else (arg if arg.startswith('http') else None)
        if not url:
            print("请提供竞品链接")
            print(__doc__)
            sys.exit(1)
        category = sys.argv[3] if arg == '采集' and len(sys.argv) > 3 else ''
        print(f"开始采集: {url}")
        result = monitor(url, category)
        print(f"\n采集结果:")
        print(f"  名称: {result.get('name')}")
        print(f"  评分: {result.get('score')}")
        print(f"  好评率: {result.get('good_rate')}%")
        print(f"  已买: {result.get('buyers')}")
        print(f"  续订率: {result.get('renewal_rate')}%")
        print(f"  价格: {result.get('price')}")
        if result.get('price_matrix'):
            print(f"  价格矩阵: {len(result['price_matrix'])} 条记录")
        if result.get('report_path'):
            print(f"  报告: {result['report_path']}")

    else:
        print(f"未知命令: {arg}")
        print(__doc__)
