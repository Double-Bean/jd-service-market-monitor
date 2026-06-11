"""
竞品报告 HTML 模板
"""
REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>竞品报告 - {name}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Microsoft YaHei',sans-serif; background:#f0f2f5; color:#333; padding:20px; }}
.container {{ max-width:1000px; margin:0 auto; }}
h1 {{ text-align:center; color:#1F4E79; margin-bottom:5px; font-size:24px; }}
.subtitle {{ text-align:center; color:#888; margin-bottom:20px; }}
.card {{ background:white; border-radius:12px; padding:24px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
.card h2 {{ color:#1F4E79; margin-bottom:16px; font-size:18px; border-bottom:2px solid #e8e8e8; padding-bottom:8px; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:14px; margin-bottom:20px; }}
.metric {{ color:white; padding:18px; border-radius:10px; text-align:center; }}
.metric.purple {{ background:linear-gradient(135deg,#667eea,#764ba2); }}
.metric.green {{ background:linear-gradient(135deg,#11998e,#38ef7d); }}
.metric.blue {{ background:linear-gradient(135deg,#4facfe,#00f2fe); }}
.metric.orange {{ background:linear-gradient(135deg,#f093fb,#f5576c); }}
.metric .value {{ font-size:26px; font-weight:bold; }}
.metric .label {{ font-size:12px; opacity:.9; }}
.sub-scores {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.sub-score {{ text-align:center; padding:14px; background:#f8f9ff; border-radius:8px; }}
.sub-score .score-val {{ font-size:22px; font-weight:bold; color:#4472C4; }}
.sub-score .score-label {{ font-size:12px; color:#666; margin-top:4px; }}
table {{ width:100%; border-collapse:collapse; margin-top:10px; }}
th {{ background:#4472C4; color:white; padding:10px 14px; text-align:left; font-size:13px; }}
td {{ padding:8px 14px; border-bottom:1px solid #eee; font-size:13px; }}
.tag {{ display:inline-block; background:#e8f0fe; color:#1a73e8; padding:3px 10px; border-radius:16px; font-size:12px; margin:2px; }}
.tag.success {{ background:#e8f5e9; color:#2e7d32; }}
.tag.warning {{ background:#fff3e0; color:#e65100; }}
.tag.danger {{ background:#fce4ec; color:#c62828; }}
.comment {{ border-left:3px solid #4472C4; padding:10px 14px; margin:8px 0; background:#f8f9ff; border-radius:0 8px 8px 0; }}
.comment .user {{ font-weight:bold; color:#1F4E79; font-size:13px; }}
.comment .meta {{ font-size:11px; color:#888; margin-top:3px; }}
.comment .content {{ margin-top:5px; line-height:1.5; font-size:13px; }}
.chart-box {{ height:280px; margin:16px 0; }}
</style></head><body><div class="container">
<h1>🔍 竞品报告</h1>
<p class="subtitle">{name} | 采集时间: {recorded_at}</p>
<div class="card"><h2>📊 核心指标</h2><div class="metrics">
<div class="metric purple"><div class="value">{score}</div><div class="label">⭐ 评分</div></div>
<div class="metric green"><div class="value">{good_rate}%</div><div class="label">👍 好评率</div></div>
<div class="metric blue"><div class="value">{buyers}+人</div><div class="label">👥 已买</div></div>
<div class="metric orange"><div class="value">{renewal_rate}%</div><div class="label">🔄 续订率</div></div>
<div class="metric purple"><div class="value">{total_comments}</div><div class="label">💬 评价</div></div>
</div></div>
<div class="card"><h2>📊 评分详情</h2><div class="sub-scores">{sub_scores}</div></div>
<div class="card"><h2>💬 评价分布</h2>
<table><tr><th>好评</th><th>中评</th><th>差评</th><th>好评率</th></tr>
<tr><td><span class="tag success">👍 {good_comments}</span></td>
<td><span class="tag warning">😐 {mid_comments}</span></td>
<td><span class="tag danger">👎 {bad_comments}</span></td>
<td><strong>{good_rate}%</strong></td></tr></table>
<div class="chart-box"><canvas id="pieChart"></canvas></div></div>
<div class="card"><h2>💰 价格与版本</h2>
<p style="margin-bottom:10px;color:#888;">价格区间: <strong style="color:#e53935;">{price}</strong></p>
<table><tr><th>版本\\周期</th>{period_headers}</tr>
{price_matrix_rows}
</table>
<p style="margin-top:8px;"><strong>可选版本:</strong> {versions_html}</p>
</div>
<div class="card"><h2>🏢 服务商</h2>
<table><tr><th>名称</th><th>电话</th><th>区域</th></tr>
<tr><td>{provider_name}</td><td>{provider_phone}</td><td>{provider_area}</td></tr></table></div>
{activities_section}
<div class="card"><h2>📝 最新评价</h2>{comments_section}</div>
</div>
<script>
new Chart(document.getElementById('pieChart'),{{
    type:'doughnut',
    data:{{labels:['好评','中评','差评'],datasets:[{{data:[{good_comments},{mid_comments},{bad_comments}],backgroundColor:['#28a745','#ffc107','#dc3545'],borderWidth:0}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'bottom'}}}}}}
}});
</script>
</body></html>"""

COMPARISON_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>竞品对比表</title>
<style>
body {{ font-family:-apple-system,'Microsoft YaHei',sans-serif; background:#f0f2f5; padding:20px; }}
.container {{ max-width:1200px; margin:0 auto; }}
h1 {{ text-align:center; color:#1F4E79; margin-bottom:20px; }}
table {{ width:100%; border-collapse:collapse; background:white; border-radius:12px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
th {{ background:#4472C4; color:white; padding:12px 10px; font-size:13px; white-space:nowrap; }}
td {{ padding:10px; border-bottom:1px solid #eee; font-size:13px; text-align:center; }}
tr:hover {{ background:#f5f8ff; }}
</style></head><body><div class="container">
<h1>🔍 竞品对比表</h1>
<table>
<tr><th>名称</th><th>分类</th><th>评分</th><th>好评率</th><th>评价数</th><th>已买</th><th>续订率</th><th>价格</th><th>服务商</th><th>采集时间</th></tr>
{table_rows}
</table>
</div></body></html>"""
