"""
竞品监控 Agent - 核心模块

功能：
- 输入竞品链接 + 需要的信息 → 自动采集 → 返回表格
- 数据持续累积保存（SQLite）
- 支持生成 HTML 报告和可视化图表

使用方式：
  from competitor_agent import CompetitorAgent
  agent = CompetitorAgent()
  
  # 采集竞品
  result = agent.monitor("https://fw.jd.com/market/new/detail/FW_GOODS-xxxx")
  
  # 查看所有竞品表格
  table = agent.get_all_table()
  
  # 生成报告
  agent.generate_report("示例商品名称")
  
  # 生成可视化
  agent.generate_chart()
"""
import os
import re
import json
import asyncio
import logging
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import create_engine, Column, Integer, Float, String, Text, DateTime, JSON, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

# ========== 数据库 ==========
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "competitor_data.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Base = declarative_base()
Session = sessionmaker(bind=engine)


class CompetitorRecord(Base):
    """竞品记录（每次采集一条）"""
    __tablename__ = "competitor_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(500), nullable=False)
    name = Column(String(500), comment="商品名称")
    platform = Column(String(50), default="fw")
    category = Column(String(100), comment="分类")

    # 核心指标
    score = Column(Float, comment="综合评分")
    sub_scores = Column(JSON, comment="子评分")
    good_rate = Column(Float, comment="好评率%")
    total_comments = Column(Integer, comment="评价总数")
    good_comments = Column(Integer, comment="好评数")
    mid_comments = Column(Integer, comment="中评数")
    bad_comments = Column(Integer, comment="差评数")
    buyers = Column(Integer, comment="已买人数")
    renewal_rate = Column(Float, comment="续订率%")

    # 价格
    price_min = Column(Float, comment="最低价")
    price_max = Column(Float, comment="最高价")
    price_display = Column(String(100), comment="价格显示")
    versions = Column(JSON, comment="版本列表")
    periods = Column(JSON, comment="周期选项")

    # 其他
    provider_name = Column(String(200), comment="服务商")
    provider_phone = Column(String(50))
    provider_area = Column(String(100))
    activities = Column(JSON, comment="活动")
    comment_details = Column(JSON, comment="评价详情")
    extra_info = Column(JSON, comment="额外信息")

    recorded_at = Column(DateTime, default=datetime.now)

Base.metadata.create_all(engine)


# ========== 采集器 (CDP 方案) ==========
async def _cdp_send(ws, method, params=None, msg_id=1):
    """发送 CDP 命令"""
    import websockets
    cmd = {"id": msg_id, "method": method}
    if params:
        cmd["params"] = params
    await ws.send(json.dumps(cmd))
    while True:
        response = await ws.recv()
        data = json.loads(response)
        if data.get("id") == msg_id:
            return data

async def _cdp_eval(ws, expression, counter=[0]):
    """通过 CDP 执行 JavaScript"""
    counter[0] += 1
    r = await _cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    }, counter[0])
    result = r.get("result", {})
    if "exceptionDetails" in result:
        exc = result["exceptionDetails"]
        return {"error": str(exc.get("text", ""))}
    return result.get("result", {}).get("value")

async def _cdp_click(ws, x, y, counter=[0]):
    """通过 CDP 模拟鼠标点击"""
    counter[0] += 1
    await _cdp_send(ws, "Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1,
    }, counter[0])
    counter[0] += 1
    await _cdp_send(ws, "Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1,
    }, counter[0])


def _fmt_price(val):
    """格式化价格：保留2位小数，不四舍五入，去掉末尾多余的零"""
    if val is None:
        return '-'
    from decimal import Decimal, ROUND_DOWN
    try:
        d = Decimal(str(float(val))).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        s = str(d)
        if s.endswith('.00'):
            return s[:-3]
        return s
    except Exception:
        return str(val)


async def _scrape_fw(url: str) -> Dict[str, Any]:
    """通过 CDP (Chrome DevTools Protocol) 采集 fw.jd.com"""
    import websockets
    import urllib.request

    # 连接到 CDP 页面列表端点；默认使用本机浏览器 9222 端口。
    cdp_list_url = os.environ.get("JD_MONITOR_CDP_LIST_URL", "http://127.0.0.1:9222/json/list")
    try:
        pages = json.loads(urllib.request.urlopen(cdp_list_url).read())
    except Exception as e:
        logger.error(f"无法连接 CDP: {e}")
        return {'title': '', 'score': None, 'sub_scores': {}, 'comments': {},
                'buyers': 0, 'renewal_rate': None, 'price': {'min': 0, 'max': 0, 'display': ''},
                'versions': [], 'periods': [], 'price_matrix': [],
                'provider': {'name': '', 'phone': '', 'area': ''}, 'activities': [],
                'comment_details': []}

    # 使用第一个真实网页标签页，跳过 Edge/Chrome 扩展后台页。
    target = next((
        p for p in pages
        if p.get("type") == "page"
        and p.get("webSocketDebuggerUrl")
        and not str(p.get("url", "")).startswith("chrome-extension://")
    ), None)
    if not target:
        logger.error("CDP 已连接，但未找到可用的网页标签页")
        return {'title': '', 'score': None, 'sub_scores': {}, 'comments': {},
                'buyers': 0, 'renewal_rate': None, 'price': {'min': 0, 'max': 0, 'display': ''},
                'versions': [], 'periods': [], 'price_matrix': [],
                'provider': {'name': '', 'phone': '', 'area': ''}, 'activities': [],
                'comment_details': []}
    ws_url = target["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url, max_size=50*1024*1024) as ws:
        await _cdp_send(ws, "Runtime.enable")
        await _cdp_send(ws, "Page.enable")
        await _cdp_send(ws, "Input.enable")

        # 导航到目标页面
        await _cdp_send(ws, "Page.navigate", {"url": url})
        await asyncio.sleep(8)

        result = {
            'title': await _cdp_eval(ws, "document.title"),
            'score': None, 'sub_scores': {},
            'comments': {'total': 0, 'good': 0, 'mid': 0, 'bad': 0, 'good_rate': 0},
            'comment_details': [], 'buyers': 0, 'renewal_rate': None,
            'price': {'min': 0, 'max': 0, 'display': ''},
            'versions': [], 'periods': [], 'price_matrix': [],
            'provider': {'name': '', 'phone': '', 'area': ''},
            'activities': [],
        }

        # 获取页面文本
        body_text = await _cdp_eval(ws, "document.body.innerText") or ""

        # 评分
        score = await _cdp_eval(ws, """(() => {
            const el = document.querySelector('.summary__score');
            return el ? parseFloat(el.textContent.trim()) : null;
        })()""")
        if score and not isinstance(score, dict):
            result['score'] = float(score)

        # 子评分
        sub_scores = await _cdp_eval(ws, """(() => {
            const items = document.querySelectorAll('.other-score-item__text');
            const r = {};
            for (const item of items) {
                const m = item.textContent.trim().match(/(.+?)\\s*(\\d+\\.?\\d*)分/);
                if (m) r[m[1]] = parseFloat(m[2]);
            }
            return r;
        })()""")
        if isinstance(sub_scores, dict):
            result['sub_scores'] = sub_scores

        # 评价数
        comments = await _cdp_eval(ws, """(() => {
            const tabs = document.querySelectorAll('.filter-tab-item');
            const r = {good: 0, mid: 0, bad: 0};
            for (const tab of tabs) {
                const text = tab.textContent.trim();
                const num = text.replace(/[^\\d]/g, '');
                if (text.includes('好评')) r.good = parseInt(num) || 0;
                if (text.includes('中评')) r.mid = parseInt(num) || 0;
                if (text.includes('差评')) r.bad = parseInt(num) || 0;
            }
            return r;
        })()""")
        if isinstance(comments, dict):
            result['comments'] = comments
            c = result['comments']
            c['total'] = c['good'] + c['mid'] + c['bad']
            c['good_rate'] = round(c['good']/c['total']*100, 1) if c['total'] > 0 else 0

        # 已买
        m = re.search(r'(\d[\d.]*\s*万?\+?)\s*人\s*已买', body_text)
        if m:
            result['buyers'] = _parse_count(m.group(1))

        # 续订率
        m = re.search(r'(\d+\.?\d*)\s*%\s*续订率', body_text)
        if m:
            result['renewal_rate'] = float(m.group(1))

        # 获取版本选项（通过 sale-info-box-title="版本" 定位，用 data-text 获取名称）
        versions = await _cdp_eval(ws, """(() => {
            const r = [];
            // 找到标题为"版本"的 sale-info-box
            const boxes = document.querySelectorAll('.sale-info-box');
            for (const box of boxes) {
                const title = box.querySelector('.sale-info-box-title');
                if (title && title.textContent.trim() === '版本') {
                    for (const el of box.querySelectorAll('.sale-value')) {
                        const t = el.getAttribute('data-text') || el.textContent.trim();
                        const b = el.getBoundingClientRect();
                        if (t && b.width > 5 && b.width < 300) {
                            r.push({text: t, x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2)});
                        }
                    }
                    break;
                }
            }
            // 回退：如果没找到 sale-info-box 结构，用正则匹配
            if (r.length === 0) {
                for (const el of document.querySelectorAll('.sale-value')) {
                    const t = el.getAttribute('data-text') || el.textContent.trim();
                    if (/^(基础版|高级版|专业版|旗舰版|标准版|初级版|豪华版|联合)/.test(t) && t.length < 30) {
                        const b = el.getBoundingClientRect();
                        if (b.width > 5 && b.width < 300) r.push({text: t, x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2)});
                    }
                }
            }
            return r;
        })()""")

        # 获取周期选项（通过 sale-info-box-title="周期" 定位，用 data-text 获取名称）
        periods = await _cdp_eval(ws, """(() => {
            const r = [];
            const boxes = document.querySelectorAll('.sale-info-box');
            for (const box of boxes) {
                const title = box.querySelector('.sale-info-box-title');
                if (title && (title.textContent.trim() === '周期' || title.textContent.trim() === '订购周期')) {
                    for (const el of box.querySelectorAll('.sale-value')) {
                        const t = el.getAttribute('data-text') || el.textContent.trim();
                        const b = el.getBoundingClientRect();
                        if (t && b.width > 5) {
                            r.push({text: t, x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2)});
                        }
                    }
                    break;
                }
            }
            // 回退：用正则匹配
            if (r.length === 0) {
                for (const el of document.querySelectorAll('.sale-value')) {
                    const t = el.getAttribute('data-text') || el.textContent.trim();
                    if (/^(免费试用\\d+天|一个月|一季度|半年|一年|\\d+天|\\d+个月)$/.test(t)) {
                        const b = el.getBoundingClientRect();
                        if (b.width > 5) r.push({text: t, x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2)});
                    }
                }
            }
            return r;
        })()""")

        if isinstance(versions, list):
            result['versions'] = [v['text'] for v in versions]
        if isinstance(periods, list):
            result['periods'] = [p['text'] for p in periods]

        # 采集价格矩阵（逐个点击版本×周期组合）
        # 关键：每次切换版本后重新获取周期列表（不同版本下可选周期可能不同）
        price_matrix = []
        all_periods = [p['text'] for p in periods] if isinstance(periods, list) else []
        collected_periods = set()  # 已采集过的版本×周期组合

        if isinstance(versions, list) and isinstance(periods, list) and versions and periods:
            for ver in versions:
                ver_text = ver['text']
                try:
                    # 点击版本
                    await _cdp_eval(ws, f"""(() => {{
                        const target = document.querySelector('.sale-value[data-text="{ver_text}"]');
                        if (!target) return 'not-found';
                        if (target.classList.contains('is-disabled')) return 'disabled';
                        if (target.classList.contains('is-check')) {{
                            const verBox = target.closest('.sale-info-box');
                            const siblings = verBox ? verBox.querySelectorAll('.sale-value[data-text]') : [];
                            let switched = false;
                            for (const el of siblings) {{
                                if (el !== target && !el.classList.contains('is-disabled')) {{
                                    el.click();
                                    switched = true;
                                    break;
                                }}
                            }}
                            if (!switched) return 'already-selected';
                        }}
                        target.click();
                        return 'ok';
                    }})()""")
                    await asyncio.sleep(2.5)

                    # 重新获取当前版本下的周期列表（动态变化）
                    current_periods = await _cdp_eval(ws, """(() => {
                        const r = [];
                        const boxes = document.querySelectorAll('.sale-info-box');
                        for (const box of boxes) {
                            const title = box.querySelector('.sale-info-box-title');
                            if (title && (title.textContent.trim() === '周期' || title.textContent.trim() === '订购周期')) {
                                for (const el of box.querySelectorAll('.sale-value')) {
                                    const t = el.getAttribute('data-text') || el.textContent.trim();
                                    if (t) r.push(t);
                                }
                                break;
                            }
                        }
                        return r;
                    })()""")

                    if not isinstance(current_periods, list):
                        current_periods = all_periods

                    # 合并去重：保留初始周期 + 当前版本新出现的周期
                    for p in current_periods:
                        if p not in all_periods:
                            all_periods.append(p)

                    logger.info(f"  版本 [{ver_text}] 可用周期: {current_periods}")

                    # 遍历当前版本下的所有周期
                    for per_text in current_periods:
                        combo_key = f"{ver_text}×{per_text}"
                        if combo_key in collected_periods:
                            continue  # 跳过已采集的组合

                        try:
                            # 点击周期
                            await _cdp_eval(ws, f"""(() => {{
                                const target = document.querySelector('.sale-value[data-text="{per_text}"]');
                                if (!target) return 'not-found';
                                if (target.classList.contains('is-disabled')) return 'disabled';
                                if (target.classList.contains('is-check')) {{
                                    // 已选中，尝试在同区域内切换到其他周期再切回
                                    const periodBox = target.closest('.sale-info-box');
                                    const siblings = periodBox ? periodBox.querySelectorAll('.sale-value[data-text]') : [];
                                    let switched = false;
                                    for (const el of siblings) {{
                                        if (el !== target && !el.classList.contains('is-disabled')) {{
                                            el.click();
                                            switched = true;
                                            break;
                                        }}
                                    }}
                                    if (!switched) {{
                                        // 同区域没有其他可点击的周期，说明当前选中状态就是正确的
                                        return 'already-selected';
                                    }}
                                }}
                                target.click();
                                return 'ok';
                            }})()""")
                            await asyncio.sleep(2.5)

                            # 检查版本或周期是否被禁用/不存在
                            disabled_info = await _cdp_eval(ws, f"""(() => {{
                                const verEl = document.querySelector('.sale-value[data-text="{ver_text}"]');
                                const perEl = document.querySelector('.sale-value[data-text="{per_text}"]');
                                return {{
                                    ver_disabled: verEl ? verEl.classList.contains('is-disabled') : false,
                                    per_disabled: perEl ? perEl.classList.contains('is-disabled') : false,
                                    per_not_found: !perEl,
                                }};
                            }})()""")

                            ver_disabled = disabled_info.get('ver_disabled', False) if isinstance(disabled_info, dict) else False
                            per_disabled = disabled_info.get('per_disabled', False) if isinstance(disabled_info, dict) else False
                            per_not_found = disabled_info.get('per_not_found', False) if isinstance(disabled_info, dict) else False

                            if ver_disabled or per_disabled or per_not_found:
                                price_matrix.append({
                                    'version': ver_text, 'period': per_text,
                                    'price': None, 'disabled': True,
                                })
                                logger.info(f"    {ver_text} × {per_text} = 不可用")
                                collected_periods.add(combo_key)
                                continue

                            # 验证版本和周期是否真正被选中（点击可能未生效）
                            check_result = await _cdp_eval(ws, f"""(() => {{
                                const verEl = document.querySelector('.sale-value[data-text="{ver_text}"]');
                                const perEl = document.querySelector('.sale-value[data-text="{per_text}"]');
                                return {{
                                    ver_checked: verEl ? verEl.classList.contains('is-check') : false,
                                    per_checked: perEl ? perEl.classList.contains('is-check') : false,
                                }};
                            }})()""")

                            ver_checked = check_result.get('ver_checked', False) if isinstance(check_result, dict) else False
                            per_checked = check_result.get('per_checked', False) if isinstance(check_result, dict) else False

                            if not ver_checked or not per_checked:
                                price_matrix.append({
                                    'version': ver_text, 'period': per_text,
                                    'price': None, 'disabled': True,
                                })
                                logger.info(f"    {ver_text} × {per_text} = 不可用 (选中失败 V={'✓' if ver_checked else '✗'} P={'✓' if per_checked else '✗'})")
                                collected_periods.add(combo_key)
                                continue

                            # 读取价格（兼容两种页面结构，含原价/划线价）
                            # 同时检查版本/周期是否有"惠"标签
                            price_info = await _cdp_eval(ws, """(() => {
                                const freeEl = document.querySelector('.price.is-free');
                                if (freeEl && freeEl.getBoundingClientRect().width > 0) {
                                    return {type: 'free'};
                                }

                                let currentPrice = null;
                                let originalPrice = null;

                                // 方式1: /main/detail/ 结构 (.sku-price__promotion + .sku-price__original-price)
                                const promoEl = document.querySelector('.sku-price__promotion .price.is-jd-font');
                                if (promoEl) {
                                    const m = promoEl.textContent.trim().match(/¥\\s*([\\d.]+)/);
                                    if (m) currentPrice = parseFloat(m[1]);
                                }
                                const origEl = document.querySelector('.sku-price__original-price');
                                if (origEl) {
                                    const m = origEl.textContent.trim().match(/([\\d.]+)/);
                                    if (m) originalPrice = parseFloat(m[1]);
                                }

                                // 方式2: /market/new/detail/ 结构 (.sku-price-wrap 内多个 .price.is-jd-font)
                                if (currentPrice === null) {
                                    const wrapEl = document.querySelector('.sku-price-wrap');
                                    const priceEls = wrapEl
                                        ? wrapEl.querySelectorAll('.price.is-jd-font')
                                        : document.querySelectorAll('.price.is-jd-font');
                                    const prices = [];
                                    for (const el of priceEls) {
                                        const m = el.textContent.trim().match(/¥\\s*([\\d.]+)/);
                                        if (m) prices.push(parseFloat(m[1]));
                                    }
                                    if (prices.length >= 1) currentPrice = prices[0];
                                    if (prices.length >= 2) originalPrice = prices[1];
                                }

                                // 检查当前选中的版本/周期是否有"惠"标签
                                let hasHui = false;
                                for (const el of document.querySelectorAll('.sale-value.is-check')) {
                                    if (el.textContent.includes('惠')) {
                                        hasHui = true;
                                        break;
                                    }
                                }

                                const hasPromotion = document.querySelector('.price-info.has-promotion') !== null;

                                return {
                                    type: 'paid',
                                    currentPrice: currentPrice,
                                    originalPrice: originalPrice,
                                    hasHui: hasHui,
                                    hasPromotion: hasPromotion,
                                };
                            })()""")

                            if isinstance(price_info, dict) and price_info.get('type') == 'free':
                                price_matrix.append({
                                    'version': ver_text, 'period': per_text,
                                    'price': 0, 'is_free': True,
                                })
                                logger.info(f"    {ver_text} × {per_text} = 免费")
                            elif isinstance(price_info, dict) and price_info.get('type') == 'paid':
                                main_price = price_info.get('currentPrice')
                                original_price = price_info.get('originalPrice')
                                has_hui = price_info.get('hasHui', False)
                                # 只有标"惠"的才有折扣，不能仅凭有两个价格就判断
                                has_discount = bool(has_hui and original_price and main_price and original_price > main_price)

                                price_matrix.append({
                                    'version': ver_text, 'period': per_text,
                                    'price': main_price, 'original_price': original_price,
                                    'has_discount': has_discount,
                                })
                                disc_str = f" (原价¥{original_price})" if has_discount else ""
                                logger.info(f"    {ver_text} × {per_text} = ¥{main_price}{disc_str}")
                            else:
                                price_matrix.append({
                                    'version': ver_text, 'period': per_text, 'price': None,
                                })
                                logger.info(f"    {ver_text} × {per_text} = N/A")

                            collected_periods.add(combo_key)

                        except Exception as e:
                            price_matrix.append({
                                'version': ver_text, 'period': per_text,
                                'price': None, 'error': str(e),
                            })
                            logger.warning(f"    {ver_text} × {per_text} = ERROR: {e}")
                            collected_periods.add(combo_key)

                except Exception as e:
                    logger.warning(f"  版本 [{ver_text}] 采集失败: {e}")

        # 更新周期列表（合并所有版本下出现的周期）
        result['periods'] = all_periods

        result['price_matrix'] = price_matrix

        # 计算价格区间
        valid_prices = [p['price'] for p in price_matrix if p['price'] is not None and p['price'] > 0]
        if valid_prices:
            result['price']['min'] = min(valid_prices)
            result['price']['max'] = max(valid_prices)
            result['price']['display'] = "¥{} - ¥{}".format(_fmt_price(min(valid_prices)), _fmt_price(max(valid_prices)))
        else:
            price_match = re.search(r'¥\s*(\d+\.?\d*)\s*[-—]\s*¥\s*(\d+\.?\d*)', body_text)
            if price_match:
                result['price']['min'] = float(price_match.group(1))
                result['price']['max'] = float(price_match.group(2))
                result['price']['display'] = "¥{} - ¥{}".format(price_match.group(1), price_match.group(2))

        # 服务商
        m = re.search(r'服务商\s*\n?\s*(.+?)(?:\n|资质)', body_text)
        if m: result['provider']['name'] = m.group(1).strip()
        m = re.search(r'客服电话\s*\n?\s*(\d[\d-]+)', body_text)
        if m: result['provider']['phone'] = m.group(1).strip()
        m = re.search(r'所在区域\s*\n?\s*(.+?)(?:\n|服务)', body_text)
        if m: result['provider']['area'] = m.group(1).strip()

        # 活动
        activities = await _cdp_eval(ws, """(() => {
            const results = [];
            const body = document.body.innerText;
            if (body.includes('免费试用')) results.push('免费试用');
            if (body.includes('关注送券')) results.push('关注送券');
            if (body.includes('互动抽奖')) results.push('互动抽奖');
            if (body.includes('会员礼包')) results.push('会员礼包');
            const els = document.querySelectorAll('[class*="discount"], [class*="coupon"], [class*="tag"], [class*="activity"], [class*="badge"]');
            for (const el of els) {
                const text = el.textContent.trim();
                if (text && text.length > 1 && text.length < 30 && !results.includes(text)) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.y < 600) results.push(text);
                }
            }
            return results.slice(0, 20);
        })()""")
        if isinstance(activities, list):
            result['activities'] = activities

        # 评价详情
        comment_details = await _cdp_eval(ws, """(() => {
            const results = [];
            const evals = document.querySelectorAll('.evaluation-item');
            for (let i = 0; i < Math.min(evals.length, 20); i++) {
                try {
                    const ev = evals[i];
                    const u = ev.querySelector('.evaluation-user-name');
                    const ct = ev.querySelector('.evaluation-item-content');
                    const ex = ev.querySelector('.evaluation-item-extra-left');
                    results.push({
                        user: u ? u.textContent.trim() : '匿名',
                        content: ct ? ct.textContent.trim() : '',
                        extra: ex ? ex.textContent.trim() : '',
                    });
                } catch(e) {}
            }
            return results;
        })()""")
        if isinstance(comment_details, list):
            for cd in comment_details:
                ex = cd.get('extra', '')
                tm = re.search(r'(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})', ex)
                vm = re.search(r'(基础版|高级版|专业版)[^\d]*', ex)
                cd['time'] = tm.group(1) if tm else ''
                cd['version'] = vm.group(0).strip() if vm else ''
                cd.pop('extra', None)
            result['comment_details'] = comment_details

        return result


def _parse_count(text: str) -> int:
    text = text.strip().replace(',', '').replace('+', '')
    if '万' in text:
        return int(float(re.sub(r'[^\d.]', '', text)) * 10000)
    return int(re.sub(r'[^\d]', '', text)) if re.sub(r'[^\d]', '', text) else 0

def _parse_float(text: str) -> float:
    c = re.sub(r'[^\d.]', '', text)
    return float(c) if c else 0.0


def _raw_price_matrix(raw: Dict[str, Any]) -> list:
    return raw.get('price_matrix') or raw.get('raw', {}).get('price_matrix') or []


def _raw_quality_issue(raw: Dict[str, Any]) -> Optional[str]:
    title = raw.get('title') or ''
    matrix = _raw_price_matrix(raw)
    usable_prices = [
        p for p in matrix
        if p.get('price') is not None and not p.get('disabled')
    ]
    if title in ('服务详情-京麦服务市场', '京麦服务市场', '服务详情'):
        return f"泛化标题: {title}"
    if not matrix:
        return "价格矩阵为空"
    if not usable_prices:
        return "没有可用价格"
    return None


# ========== Agent ==========
class CompetitorAgent:
    """竞品监控 Agent"""

    def __init__(self):
        self.session = Session()

    def close(self):
        self.session.close()

    def monitor(self, url: str, category: str = "", extra_fields: List[str] = None,
                max_attempts: int = 3, retry_wait: int = 8) -> Dict[str, Any]:
        """
        监控一个竞品链接，自动采集并保存

        Args:
            url: 竞品链接
            category: 产品线分类
            extra_fields: 额外需要采集的字段

        Returns:
            dict: 采集结果
        """
        logger.info(f"🔍 开始监控: {url}")

        # 判断平台
        if 'fw.jd.com' in url or 'FW_GOODS' in url:
            platform = 'fw'
        elif 'item.jd.com' in url:
            platform = 'jd'
        else:
            platform = 'other'

        # 采集。京东服务市场偶尔先返回通用空壳页；保存前先质量检查并重试。
        if platform == 'fw':
            raw = None
            last_issue = None
            for attempt in range(1, max_attempts + 1):
                raw = asyncio.run(_scrape_fw(url))
                last_issue = _raw_quality_issue(raw)
                if not last_issue:
                    if attempt > 1:
                        logger.info(f"✅ 第 {attempt}/{max_attempts} 次重试后采集成功")
                    break
                if attempt < max_attempts:
                    logger.warning(
                        f"⚠️ 第 {attempt}/{max_attempts} 次采集疑似空壳（{last_issue}），"
                        f"等待 {retry_wait}s 后重试: {url}"
                    )
                    time.sleep(retry_wait)
                else:
                    logger.error(f"❌ 连续 {max_attempts} 次采集仍疑似空壳（{last_issue}），保留最后一次结果: {url}")
            raw['quality_issue'] = last_issue
            raw['attempts'] = max_attempts if last_issue else attempt
        else:
            raw = {'title': '', 'score': None, 'comments': {}, 'buyers': 0,
                   'renewal_rate': None, 'price': {}, 'versions': [], 'periods': [],
                   'provider': {}, 'activities': [], 'sub_scores': {}, 'comment_details': []}

        # 保存到数据库
        record = CompetitorRecord(
            url=url,
            name=raw.get('title', ''),
            platform=platform,
            category=category,
            score=raw.get('score'),
            sub_scores=raw.get('sub_scores', {}),
            good_rate=raw.get('comments', {}).get('good_rate', 0),
            total_comments=raw.get('comments', {}).get('total', 0),
            good_comments=raw.get('comments', {}).get('good', 0),
            mid_comments=raw.get('comments', {}).get('mid', 0),
            bad_comments=raw.get('comments', {}).get('bad', 0),
            buyers=raw.get('buyers', 0),
            renewal_rate=raw.get('renewal_rate'),
            price_min=raw.get('price', {}).get('min', 0),
            price_max=raw.get('price', {}).get('max', 0),
            price_display=raw.get('price', {}).get('display', ''),
            versions=raw.get('versions', []),
            periods=raw.get('periods', []),
            provider_name=raw.get('provider', {}).get('name', ''),
            provider_phone=raw.get('provider', {}).get('phone', ''),
            provider_area=raw.get('provider', {}).get('area', ''),
            activities=raw.get('activities', []),
            comment_details=raw.get('comment_details', []),
            extra_info={'raw': raw, 'extra_fields': extra_fields, 'price_matrix': raw.get('price_matrix', [])},
        )
        self.session.add(record)
        self.session.commit()

        price_matrix = raw.get('price_matrix', [])
        result = {
            'status': 'success',
            'name': record.name,
            'url': url,
            'score': record.score,
            'good_rate': record.good_rate,
            'total_comments': record.total_comments,
            'buyers': record.buyers,
            'renewal_rate': record.renewal_rate,
            'price': record.price_display,
            'price_matrix': price_matrix,
            'versions': raw.get('versions', []),
            'periods': raw.get('periods', []),
            'provider': record.provider_name,
            'recorded_at': record.recorded_at.strftime('%Y-%m-%d %H:%M'),
            'quality_issue': raw.get('quality_issue'),
            'attempts': raw.get('attempts', 1),
        }
        logger.info(f"✅ 监控完成: {record.name}")
        return result

    def get_all_table(self) -> List[Dict]:
        """获取所有竞品最新数据表格"""
        from sqlalchemy import func
        # 每个URL取最新一条
        subq = self.session.query(
            CompetitorRecord.url,
            func.max(CompetitorRecord.id).label('max_id')
        ).group_by(CompetitorRecord.url).subquery()

        records = self.session.query(CompetitorRecord).join(
            subq, CompetitorRecord.id == subq.c.max_id
        ).order_by(CompetitorRecord.recorded_at.desc()).all()

        return [{
            '名称': r.name or '-',
            '分类': r.category or '-',
            '评分': r.score,
            '好评率': f"{r.good_rate}%" if r.good_rate else '-',
            '评价数': r.total_comments or 0,
            '好评/中/差': f"{r.good_comments or 0}/{r.mid_comments or 0}/{r.bad_comments or 0}",
            '已买人数': r.buyers or 0,
            '续订率': f"{r.renewal_rate}%" if r.renewal_rate else '-',
            '价格': r.price_display or '-',
            '版本': ', '.join([v.get('full', str(v)) if isinstance(v, dict) else str(v) for v in (r.versions or [])]) or '-',
            '周期': ', '.join(r.periods or []) if r.periods else '-',
            '服务商': r.provider_name or '-',
            '采集时间': r.recorded_at.strftime('%m-%d %H:%M'),
        } for r in records]

    def get_history(self, url: str) -> List[Dict]:
        """获取某个竞品的历史记录"""
        records = self.session.query(CompetitorRecord).filter_by(
            url=url
        ).order_by(CompetitorRecord.recorded_at.asc()).all()
        return [{
            '时间': r.recorded_at.strftime('%Y-%m-%d %H:%M'),
            '评分': r.score,
            '好评率': r.good_rate,
            '评价数': r.total_comments,
            '已买': r.buyers,
            '续订率': r.renewal_rate,
            '价格': r.price_display,
        } for r in records]

    def get_detail(self, name: str) -> Optional[Dict]:
        """获取某个竞品的最新详细数据"""
        record = self.session.query(CompetitorRecord).filter(
            CompetitorRecord.name.contains(name)
        ).order_by(CompetitorRecord.recorded_at.desc()).first()
        if not record:
            return None
        return {
            'name': record.name,
            'url': record.url,
            'score': record.score,
            'sub_scores': record.sub_scores or {},
            'good_rate': record.good_rate,
            'total_comments': record.total_comments,
            'good_comments': record.good_comments,
            'mid_comments': record.mid_comments,
            'bad_comments': record.bad_comments,
            'buyers': record.buyers,
            'renewal_rate': record.renewal_rate,
            'price_display': record.price_display,
            'versions': record.versions or [],
            'periods': record.periods or [],
            'provider_name': record.provider_name,
            'provider_phone': record.provider_phone,
            'provider_area': record.provider_area,
            'activities': record.activities or [],
            'comment_details': record.comment_details or [],
            'extra_info': record.extra_info or {},
            'recorded_at': record.recorded_at.strftime('%Y-%m-%d %H:%M'),
        }

    def generate_html_report(self, name: str) -> str:
        """生成某个竞品的 HTML 详细报告"""
        from competitor_agent_templates import REPORT_TEMPLATE
        detail = self.get_detail(name)
        if not detail:
            return ""
        d = detail
        versions_html = ''.join(
            '<span class="tag">{}</span>'.format(v.get("full", v) if isinstance(v, dict) else v)
            for v in d['versions']
        )
        periods_html = ', '.join(d['periods']) if d['periods'] else '-'
        comments_html = ''
        for c in (d['comment_details'] or [])[:10]:
            comments_html += (
                '<div class="comment">'
                '<div class="user">{}</div>'
                '<div class="meta">{} | {}</div>'
                '<div class="content">{}</div>'
                '</div>'
            ).format(c.get('user','匿名'), c.get('time',''), c.get('version',''), c.get('content',''))
        sub_scores_html = ''
        for k, v in d.get('sub_scores', {}).items():
            sub_scores_html += '<div class="sub-score"><div class="score-val">{}</div><div class="score-label">{}</div></div>'.format(v, k)
        activities_section = ''
        if d['activities']:
            tags = ''.join('<span class="tag success">{}</span>'.format(a) for a in d['activities'])
            activities_section = '<div class="card"><h2>🎯 活动</h2><div>{}</div></div>'.format(tags)
        comments_section = comments_html if comments_html else '<p style="color:#888;">暂无评价</p>'

        # 价格矩阵行
        price_matrix_rows = ''
        versions_list_html = ''
        extra = d.get('extra_info') or {}
        matrix = extra.get('price_matrix') or extra.get('raw', {}).get('price_matrix') or []
        if matrix:
            versions_set = list(dict.fromkeys(p['version'] for p in matrix if p.get('version')))
            periods_set = list(dict.fromkeys(p['period'] for p in matrix if p.get('period')))
            # 动态生成周期表头
            period_headers = ''.join('<th>{}</th>'.format(per) for per in periods_set)
            for ver in versions_set:
                row = '<tr><td><strong>{}</strong></td>'.format(ver)
                for per in periods_set:
                    item = next((p for p in matrix if p['version'] == ver and p['period'] == per), None)
                    if item and item.get('disabled'):
                        cell = '<td style="color:#bbb;">不可用</td>'
                    elif item and item.get('is_free'):
                        cell = '<td style="color:#2e7d32;font-weight:bold;">免费</td>'
                    elif item and item.get('price') is not None:
                        price_val = item['price']
                        if price_val == 0:
                            cell = '<td style="color:#2e7d32;font-weight:bold;">免费</td>'
                        elif item.get('has_discount'):
                            orig = item.get('original_price')
                            cell = '<td><strong style="color:#e53935;">¥{}</strong> <span style="color:#bbb;text-decoration:line-through;font-size:11px;">¥{}</span></td>'.format(self._fmt_price(price_val), self._fmt_price(orig))
                        else:
                            cell = '<td><strong style="color:#e53935;">¥{}</strong></td>'.format(self._fmt_price(price_val))
                    else:
                        row += '<td>-</td>'
                        continue
                    row += cell
                row += '</tr>'
                price_matrix_rows += row
            versions_list_html = ', '.join('<span class="tag">{}</span>'.format(v) for v in versions_set)
        else:
            price_matrix_rows = '<tr><td colspan="6" style="color:#888;">暂未采集版本×周期价格矩阵</td></tr>'
            versions_list_html = versions_html or '-'
            period_headers = '<th>一个月</th><th>一季度</th><th>半年</th><th>一年</th>'

        return REPORT_TEMPLATE.format(
            name=d['name'] or '', recorded_at=d['recorded_at'] or '',
            score=d['score'] or '-', good_rate=d['good_rate'] or 0,
            buyers='{:,}'.format(d['buyers']) if d['buyers'] else '-',
            renewal_rate=d['renewal_rate'] or 0,
            total_comments='{:,}'.format(d['total_comments']) if d['total_comments'] else '-',
            sub_scores=sub_scores_html,
            good_comments=d['good_comments'] or 0,
            mid_comments=d['mid_comments'] or 0,
            bad_comments=d['bad_comments'] or 0,
            price=d['price_display'] or '-',
            price_matrix_rows=price_matrix_rows,
            period_headers=period_headers,
            versions_html=versions_list_html,
            provider_name=d['provider_name'] or '-',
            provider_phone=d['provider_phone'] or '-',
            provider_area=d['provider_area'] or '-',
            activities_section=activities_section,
            comments_section=comments_section,
        )

    def generate_comparison_html(self) -> str:
        """生成所有竞品对比表格的 HTML"""
        from competitor_agent_templates import COMPARISON_TEMPLATE
        rows = self.get_all_table()
        if not rows:
            return "<p>暂无数据</p>"
        trs = ''
        for r in rows:
            trs += '<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{:,}</td><td>{:,}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>'.format(
                r['名称'], r['分类'], r['评分'], r['好评率'],
                r['评价数'], r['已买人数'], r['续订率'],
                r['价格'], r['服务商'], r['采集时间']
            )
        return COMPARISON_TEMPLATE.format(table_rows=trs)

    @staticmethod
    def _record_price_matrix(record) -> list:
        extra = record.extra_info or {}
        return extra.get('price_matrix') or extra.get('raw', {}).get('price_matrix') or []

    @staticmethod
    def _record_quality(record) -> tuple:
        matrix = CompetitorAgent._record_price_matrix(record)
        usable_prices = [
            p for p in matrix
            if p.get('price') is not None and not p.get('disabled')
        ]
        generic_name = record.name in ('服务详情-京麦服务市场', '服务详情')
        return (
            1 if usable_prices else 0,
            1 if matrix else 0,
            0 if generic_name else 1,
            record.recorded_at,
        )

    @staticmethod
    def _pick_daily_record(records: list):
        return max(records, key=CompetitorAgent._record_quality) if records else None

    def detect_changes(self, url: str = None, name: str = None, target_date=None) -> Dict[str, Any]:
        """
        检测某个竞品今天与上一个有记录日期的数据变化

        Args:
            url: 竞品链接（与 name 二选一）
            name: 商品名称（与 url 二选一）
            target_date: 对比日期，默认今天；当今天没有记录时回退到最新记录日期

        Returns:
            dict: 变化信息
        """
        query = self.session.query(CompetitorRecord)
        if url:
            query = query.filter_by(url=url)
        elif name:
            query = query.filter(CompetitorRecord.name.like(f"%{name}%"))
        else:
            return {'error': '请提供 url 或 name'}

        records = query.order_by(CompetitorRecord.recorded_at.desc()).all()
        if not records:
            return {'error': '暂无采集记录', 'records': 0}

        compare_date = target_date or datetime.now().date()
        today_candidates = [r for r in records if r.recorded_at.date() == compare_date]
        if not today_candidates:
            compare_date = records[0].recorded_at.date()
            today_candidates = [r for r in records if r.recorded_at.date() == compare_date]

        today = self._pick_daily_record(today_candidates)
        previous_dates = sorted(
            {r.recorded_at.date() for r in records if r.recorded_at.date() < compare_date},
            reverse=True
        )
        if not previous_dates:
            return {
                'error': '数据不足，至少需要今天和上一个有记录日期的数据',
                'records': len(records),
                'current_date': compare_date.strftime('%Y-%m-%d'),
            }

        previous_date = previous_dates[0]
        previous_candidates = [r for r in records if r.recorded_at.date() == previous_date]
        yesterday = self._pick_daily_record(previous_candidates)

        changes = {
            'name': today.name,
            'url': today.url,
            'today_date': compare_date.strftime('%Y-%m-%d'),
            'previous_date': previous_date.strftime('%Y-%m-%d'),
            'today_time': today.recorded_at.strftime('%Y-%m-%d %H:%M'),
            'yesterday_time': yesterday.recorded_at.strftime('%Y-%m-%d %H:%M'),
            'price_changes': [],
            'metric_changes': [],
            'activity_changes': [],
            'has_changes': False,
        }

        # 1. 价格矩阵变化
        today_matrix = self._record_price_matrix(today)
        yesterday_matrix = self._record_price_matrix(yesterday)

        yesterday_prices = {}
        for p in yesterday_matrix:
            key = f"{p.get('version', '')}×{p.get('period', '')}"
            yesterday_prices[key] = p

        for p in today_matrix:
            key = f"{p.get('version', '')}×{p.get('period', '')}"
            yp = yesterday_prices.get(key)

            if yp is None:
                # 新增的组合
                changes['price_changes'].append({
                    'version': p.get('version', ''), 'period': p.get('period', ''),
                    'type': 'new', 'today_price': p.get('price'), 'yesterday_price': None,
                })
                changes['has_changes'] = True
            elif p.get('disabled') != yp.get('disabled'):
                # 可用性变化
                changes['price_changes'].append({
                    'version': p.get('version', ''), 'period': p.get('period', ''),
                    'type': 'availability', 'today_price': p.get('price'), 'yesterday_price': yp.get('price'),
                    'today_disabled': p.get('disabled'), 'yesterday_disabled': yp.get('disabled'),
                })
                changes['has_changes'] = True
            elif p.get('price') != yp.get('price'):
                # 价格变化
                changes['price_changes'].append({
                    'version': p.get('version', ''), 'period': p.get('period', ''),
                    'type': 'price_change',
                    'today_price': p.get('price'), 'yesterday_price': yp.get('price'),
                    'today_original': p.get('original_price'), 'yesterday_original': yp.get('original_price'),
                })
                changes['has_changes'] = True

        # 检查消失的组合
        today_keys = {f"{p.get('version', '')}×{p.get('period', '')}" for p in today_matrix}
        for p in yesterday_matrix:
            key = f"{p.get('version', '')}×{p.get('period', '')}"
            if key not in today_keys and not p.get('disabled'):
                changes['price_changes'].append({
                    'version': p.get('version', ''), 'period': p.get('period', ''),
                    'type': 'removed', 'today_price': None, 'yesterday_price': p.get('price'),
                })
                changes['has_changes'] = True

        # 2. 核心指标变化
        metrics = [
            ('score', '评分', '', 0.1),
            ('good_rate', '好评率', '%', 0.5),
            ('total_comments', '评价总数', '', 1),
            ('buyers', '已买人数', '', 1),
            ('renewal_rate', '续订率', '%', 0.5),
        ]
        for field, label, unit, threshold in metrics:
            tv = getattr(today, field, None)
            yv = getattr(yesterday, field, None)
            if tv is not None and yv is not None and abs(tv - yv) >= threshold:
                changes['metric_changes'].append({
                    'field': field, 'label': label, 'unit': unit,
                    'today': tv, 'yesterday': yv,
                    'diff': tv - yv,
                })
                changes['has_changes'] = True

        # 3. 活动变化
        today_acts = set(today.activities or [])
        yesterday_acts = set(yesterday.activities or [])
        if today_acts != yesterday_acts:
            changes['activity_changes'] = {
                'added': list(today_acts - yesterday_acts),
                'removed': list(yesterday_acts - today_acts),
            }
            changes['has_changes'] = True

        return changes

    def generate_change_report(self, url: str = None, name: str = None) -> str:
        """
        生成变化检测的可视化 HTML 报告

        Returns:
            str: HTML 内容
        """
        changes = self.detect_changes(url=url, name=name)

        if 'error' in changes:
            return f'<html><body><h2>⚠️ {changes["error"]}</h2></body></html>'

        name = changes['name']
        has_changes = changes['has_changes']

        # 构建价格变化表格
        price_rows = ''
        for c in changes['price_changes']:
            ver = c['version']
            per = c['period']
            if c['type'] == 'new':
                price_rows += f'''<tr style="background:#e8f5e9;">
                    <td>{ver}</td><td>{per}</td>
                    <td>-</td>
                    <td><strong style="color:#2e7d32;">¥{c['today_price']}</strong> <span style="color:#2e7d32;font-size:11px;">新增</span></td>
                    <td>➕</td></tr>'''
            elif c['type'] == 'removed':
                price_rows += f'''<tr style="background:#ffebee;">
                    <td>{ver}</td><td>{per}</td>
                    <td><strong>¥{c['yesterday_price']}</strong></td>
                    <td>-</td>
                    <td>➖ 已下架</td></tr>'''
            elif c['type'] == 'availability':
                td = c.get('today_disabled')
                yd = c.get('yesterday_disabled')
                if td and not yd:
                    price_rows += f'''<tr style="background:#fff3e0;">
                        <td>{ver}</td><td>{per}</td>
                        <td>¥{c['yesterday_price'] or '-'}</td>
                        <td>不可用</td>
                        <td>⚠️ 变为不可用</td></tr>'''
                elif not td and yd:
                    price_rows += f'''<tr style="background:#e8f5e9;">
                        <td>{ver}</td><td>{per}</td>
                        <td>不可用</td>
                        <td>¥{c['today_price']}</td>
                        <td>✅ 恢复可用</td></tr>'''
            elif c['type'] == 'price_change':
                tp = c['today_price'] or 0
                yp = c['yesterday_price'] or 0
                diff = tp - yp
                if diff > 0:
                    arrow = '🔺'
                    color = '#e53935'
                    bg = '#ffebee'
                elif diff < 0:
                    arrow = '🔻'
                    color = '#2e7d32'
                    bg = '#e8f5e9'
                else:
                    arrow = '➡️'
                    color = '#666'
                    bg = '#fff'
                to_orig = f" <span style='color:#bbb;text-decoration:line-through;font-size:11px;'>¥{c['today_original']}</span>" if c.get('today_original') else ""
                yo_orig = f" <span style='color:#bbb;text-decoration:line-through;font-size:11px;'>¥{c['yesterday_original']}</span>" if c.get('yesterday_original') else ""
                price_rows += f'''<tr style="background:{bg};">
                    <td>{ver}</td><td>{per}</td>
                    <td>¥{yp}{yo_orig}</td>
                    <td><strong style="color:{color};">¥{tp}{to_orig}</strong></td>
                    <td>{arrow} {self._fmt_price(abs(diff))}</td></tr>'''

        # 构建指标变化
        metric_rows = ''
        for m in changes['metric_changes']:
            diff = m['diff']
            unit = m['unit']
            if diff > 0:
                arrow = '🔺'
                color = '#e53935'
            elif diff < 0:
                arrow = '🔻'
                color = '#2e7d32'
            else:
                arrow = '➡️'
                color = '#666'
            metric_rows += f'''<tr>
                <td>{m['label']}</td>
                <td>{m['yesterday']}{unit}</td>
                <td><strong style="color:{color};">{m['today']}{unit}</strong></td>
                <td>{arrow} {abs(diff):.1f}{unit}</td></tr>'''

        # 构建活动变化
        activity_html = ''
        act = changes.get('activity_changes', {})
        if act:
            if act.get('added'):
                activity_html += '<div style="margin-bottom:8px;">'
                for a in act['added']:
                    activity_html += f'<span style="display:inline-block;background:#e8f5e9;color:#2e7d32;padding:4px 12px;border-radius:20px;margin:2px;font-size:13px;">✅ + {a}</span>'
                activity_html += '</div>'
            if act.get('removed'):
                activity_html += '<div>'
                for a in act['removed']:
                    activity_html += f'<span style="display:inline-block;background:#ffebee;color:#e53935;padding:4px 12px;border-radius:20px;margin:2px;font-size:13px;">❌ - {a}</span>'
                activity_html += '</div>'

        # 总体状态
        if has_changes:
            status_html = '''<div style="background:#fff3e0;border:1px solid #ffb74d;border-radius:8px;padding:12px 20px;margin-bottom:20px;display:flex;align-items:center;gap:10px;">
                <span style="font-size:24px;">⚠️</span>
                <div><strong style="color:#e65100;">检测到变化</strong><br><span style="color:#666;font-size:13px;">以下数据与上次采集相比发生了变化</span></div>
            </div>'''
        else:
            status_html = '''<div style="background:#e8f5e9;border:1px solid #81c784;border-radius:8px;padding:12px 20px;margin-bottom:20px;display:flex;align-items:center;gap:10px;">
                <span style="font-size:24px;">✅</span>
                <div><strong style="color:#2e7d32;">无变化</strong><br><span style="color:#666;font-size:13px;">所有数据与上次采集一致</span></div>
            </div>'''

        html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>竞品变化监控 - {name}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Microsoft YaHei','PingFang SC',sans-serif; background:#f0f2f5; color:#333; padding:20px; }}
.container {{ max-width:1100px; margin:0 auto; }}
h1 {{ text-align:center; color:#1F4E79; margin-bottom:6px; font-size:22px; }}
.subtitle {{ text-align:center; color:#999; font-size:13px; margin-bottom:20px; }}
.card {{ background:white; border-radius:12px; padding:24px; margin-bottom:16px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.card h2 {{ color:#1F4E79; margin-bottom:14px; font-size:16px; border-bottom:2px solid #e8e8e8; padding-bottom:8px; display:flex; align-items:center; gap:8px; }}
.card h2 .icon {{ font-size:18px; }}
table {{ width:100%; border-collapse:collapse; margin-top:8px; }}
th {{ background:#4472C4; color:white; padding:10px 14px; text-align:left; font-size:13px; font-weight:500; }}
td {{ padding:10px 14px; border-bottom:1px solid #f0f0f0; font-size:13px; }}
tr:hover {{ background:#f8f9ff; }}
.empty {{ text-align:center; color:#bbb; padding:30px; font-size:14px; }}
.time-info {{ display:flex; justify-content:space-between; background:#f8f9fa; padding:10px 16px; border-radius:8px; margin-bottom:16px; font-size:13px; color:#666; }}
.time-info span {{ display:flex; align-items:center; gap:4px; }}
.legend {{ display:flex; gap:16px; margin-top:8px; font-size:12px; color:#888; }}
.legend span {{ display:flex; align-items:center; gap:4px; }}
.legend .dot {{ width:12px; height:12px; border-radius:3px; display:inline-block; }}
</style></head><body>
<div class="container">
<h1>📊 竞品变化监控</h1>
<p class="subtitle">{name}</p>

<div class="time-info">
    <span>📅 上次采集: {changes['yesterday_time']}</span>
    <span>📅 本次采集: {changes['today_time']}</span>
</div>

{status_html}

<div class="card">
    <h2><span class="icon">💰</span> 价格变化</h2>
    {'<table><tr><th>版本</th><th>周期</th><th>上次价格</th><th>本次价格</th><th>变化</th></tr>' + price_rows + '</table>' if price_rows else '<div class="empty">价格无变化</div>'}
    <div class="legend">
        <span><span class="dot" style="background:#ffebee;"></span>涨价</span>
        <span><span class="dot" style="background:#e8f5e9;"></span>降价</span>
        <span><span class="dot" style="background:#fff3e0;"></span>可用性变化</span>
    </div>
</div>

<div class="card">
    <h2><span class="icon">📈</span> 指标变化</h2>
    {'<table><tr><th>指标</th><th>上次</th><th>本次</th><th>变化</th></tr>' + metric_rows + '</table>' if metric_rows else '<div class="empty">指标无变化</div>'}
</div>

<div class="card">
    <h2><span class="icon">🎯</span> 活动变化</h2>
    {activity_html if activity_html else '<div class="empty">活动无变化</div>'}
</div>

</div>
</body></html>'''
        return html

    @staticmethod
    def _fmt_price(val):
        """格式化价格：保留2位小数，不四舍五入，去掉末尾多余的零"""
        return _fmt_price(val)

    def generate_daily_monitor_report(self) -> str:
        """
        生成所有竞品的每日变化监控报告（含价格矩阵和活动变化详情）

        Returns:
            str: HTML 内容
        """
        from sqlalchemy import func
        # 获取所有竞品（不限记录次数）
        urls = [r[0] for r in self.session.query(
            CompetitorRecord.url
        ).group_by(CompetitorRecord.url).all()]

        if not urls:
            return '<html><body style="padding:40px;text-align:center;"><h2>暂无数据</h2></body></html>'

        all_sections = ''
        change_count = 0
        new_count = 0

        for url in urls:
            changes = self.detect_changes(url=url)
            if 'error' in changes:
                # 只有1次记录，显示首次采集信息
                new_count += 1
                record = self.session.query(CompetitorRecord).filter_by(url=url).order_by(CompetitorRecord.recorded_at.desc()).first()
                if not record:
                    continue
                extra = record.extra_info or {}
                matrix = extra.get('price_matrix') or extra.get('raw', {}).get('price_matrix') or []
                
                # 构建价格概览
                price_overview = ''
                if matrix:
                    valid = [p for p in matrix if p.get('price') and not p.get('disabled')]
                    if valid:
                        prices = [p['price'] for p in valid]
                        price_overview = f"¥{min(prices)} - ¥{max(prices)}"
                
                # 构建价格矩阵表格
                price_rows = ''
                versions_set = list(dict.fromkeys(p['version'] for p in matrix if p.get('version')))
                periods_set = list(dict.fromkeys(p['period'] for p in matrix if p.get('period')))
                for ver in versions_set:
                    row = f'<td><strong>{ver}</strong></td>'
                    for per in periods_set:
                        item = next((p for p in matrix if p['version'] == ver and p['period'] == per), None)
                        if item and item.get('disabled'):
                            row += '<td style="color:#bbb;">-</td>'
                        elif item and (item.get('is_free') or item.get('price') == 0):
                            row += '<td style="color:#2e7d32;font-weight:bold;">免费</td>'
                        elif item and item.get('price') is not None:
                            pval = item['price']
                            if item.get('has_discount'):
                                row += f'<td><strong style="color:#e53935;">¥{self._fmt_price(pval)}</strong> <span style="color:#bbb;text-decoration:line-through;font-size:10px;">¥{self._fmt_price(item["original_price"])}</span></td>'
                            else:
                                row += f'<td><strong style="color:#e53935;">¥{self._fmt_price(pval)}</strong></td>'
                        else:
                            row += '<td style="color:#ddd;">-</td>'
                    row = '<tr>' + row + '</tr>'
                    price_rows += row
                
                period_headers = ''.join(f'<th>{p}</th>' for p in periods_set)
                
                price_table = ''
                if price_rows:
                    price_table = f'''<div style="margin-top:12px;">
                        <table style="width:100%;border-collapse:collapse;font-size:11px;">
                            <tr><th style="background:#5c6bc0;padding:6px 8px;color:white;text-align:left;">版本</th>{period_headers}</tr>
                            {price_rows}
                        </table></div>'''

                all_sections += f'''<div style="background:white;border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,0.06);border-left:4px solid #42a5f5;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <div style="display:flex;align-items:center;gap:8px;">
                            <span style="font-size:20px;">🆕</span>
                            <strong style="font-size:16px;color:#1F4E79;">{record.name}</strong>
                        </div>
                        <span style="color:#999;font-size:12px;">首次采集 {record.recorded_at.strftime('%Y-%m-%d %H:%M')}</span>
                    </div>
                    <p style="font-size:13px;color:#555;">评分 {record.score} | 已买 {record.buyers:,}+人 | 续订率 {record.renewal_rate}% | 价格 {price_overview or '-'}</p>
                    {price_table}
                </div>'''
                continue

            name = changes['name']
            has = changes['has_changes']

            # === 摘要 ===
            summary_parts = []
            for c in changes['price_changes']:
                if c['type'] == 'price_change':
                    tp = c['today_price'] or 0
                    yp = c['yesterday_price'] or 0
                    diff = tp - yp
                    arrow = '🔺' if diff > 0 else '🔻'
                    summary_parts.append(f"{c['version']}×{c['period']}: {arrow}¥{self._fmt_price(abs(diff))}")
                elif c['type'] == 'new':
                    summary_parts.append(f"{c['version']}×{c['period']}: 新增¥{c['today_price']}")
                elif c['type'] == 'removed':
                    summary_parts.append(f"{c['version']}×{c['period']}: 下架")
                elif c['type'] == 'availability':
                    if c.get('today_disabled'):
                        summary_parts.append(f"{c['version']}×{c['period']}: 变为不可用")
                    else:
                        summary_parts.append(f"{c['version']}×{c['period']}: 恢复可用")

            for m in changes['metric_changes']:
                diff = m['diff']
                arrow = '🔺' if diff > 0 else '🔻'
                summary_parts.append(f"{m['label']}: {arrow}{abs(diff):.1f}{m['unit']}")

            act = changes.get('activity_changes', {})
            if act:
                for a in act.get('added', []):
                    summary_parts.append(f"活动+{a}")
                for a in act.get('removed', []):
                    summary_parts.append(f"活动-{a}")

            if has:
                change_count += 1
                bg = '#fff8e1'
                border = '#ffb74d'
                status_icon = '⚠️'
            else:
                bg = '#f1f8e9'
                border = '#aed581'
                status_icon = '✅'

            summary_text = '、'.join(summary_parts) if summary_parts else '无变化'

            # === 价格矩阵变化表格 ===
            price_table_html = ''
            if changes['price_changes']:
                price_rows = ''
                for c in changes['price_changes']:
                    ver = c['version']
                    per = c['period']
                    if c['type'] == 'new':
                        price_rows += f'''<tr style="background:#e8f5e9;">
                            <td>{ver}</td><td>{per}</td>
                            <td>-</td>
                            <td><strong style="color:#2e7d32;">¥{c['today_price']}</strong></td>
                            <td><span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:10px;font-size:11px;">➕ 新增</span></td></tr>'''
                    elif c['type'] == 'removed':
                        price_rows += f'''<tr style="background:#ffebee;">
                            <td>{ver}</td><td>{per}</td>
                            <td><strong>¥{c['yesterday_price']}</strong></td>
                            <td>-</td>
                            <td><span style="background:#ffebee;color:#e53935;padding:2px 8px;border-radius:10px;font-size:11px;">➖ 下架</span></td></tr>'''
                    elif c['type'] == 'availability':
                        if c.get('today_disabled'):
                            price_rows += f'''<tr style="background:#fff3e0;">
                                <td>{ver}</td><td>{per}</td>
                                <td>¥{c['yesterday_price'] or '-'}</td>
                                <td style="color:#bbb;">不可用</td>
                                <td><span style="background:#fff3e0;color:#e65100;padding:2px 8px;border-radius:10px;font-size:11px;">⚠️ 变为不可用</span></td></tr>'''
                        else:
                            price_rows += f'''<tr style="background:#e8f5e9;">
                                <td>{ver}</td><td>{per}</td>
                                <td style="color:#bbb;">不可用</td>
                                <td>¥{c['today_price']}</td>
                                <td><span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:10px;font-size:11px;">✅ 恢复可用</span></td></tr>'''
                    elif c['type'] == 'price_change':
                        tp = c['today_price'] or 0
                        yp = c['yesterday_price'] or 0
                        diff = tp - yp
                        if diff > 0:
                            arrow, color, pill_bg, pill_color = '🔺', '#e53935', '#ffebee', '#e53935'
                            pill_text = f'+¥{self._fmt_price(diff)}'
                        else:
                            arrow, color, pill_bg, pill_color = '🔻', '#2e7d32', '#e8f5e9', '#2e7d32'
                            pill_text = f'-¥{self._fmt_price(abs(diff))}'
                        to_orig = f" <span style='color:#bbb;text-decoration:line-through;font-size:11px;'>¥{c['today_original']}</span>" if c.get('today_original') else ""
                        yo_orig = f" <span style='color:#bbb;text-decoration:line-through;font-size:11px;'>¥{c['yesterday_original']}</span>" if c.get('yesterday_original') else ""
                        price_rows += f'''<tr style="background:{pill_bg};">
                            <td>{ver}</td><td>{per}</td>
                            <td>¥{yp}{yo_orig}</td>
                            <td><strong style="color:{color};">¥{tp}{to_orig}</strong></td>
                            <td><span style="background:{pill_bg};color:{pill_color};padding:2px 8px;border-radius:10px;font-size:11px;">{arrow} {pill_text}</span></td></tr>'''

                price_table_html = f'''<div style="margin-top:12px;">
                    <table style="width:100%;border-collapse:collapse;font-size:12px;">
                        <tr><th style="background:#5c6bc0;padding:8px 10px;color:white;text-align:left;">版本</th>
                            <th style="background:#5c6bc0;padding:8px 10px;color:white;text-align:left;">周期</th>
                            <th style="background:#5c6bc0;padding:8px 10px;color:white;text-align:left;">上次价格</th>
                            <th style="background:#5c6bc0;padding:8px 10px;color:white;text-align:left;">本次价格</th>
                            <th style="background:#5c6bc0;padding:8px 10px;color:white;text-align:left;">变化</th></tr>
                        {price_rows}
                    </table></div>'''
            else:
                price_table_html = '<p style="margin-top:12px;color:#bbb;font-size:13px;">💰 价格无变化</p>'

            # === 指标变化 ===
            metric_html = ''
            if changes['metric_changes']:
                metric_rows = ''
                for m in changes['metric_changes']:
                    diff = m['diff']
                    if diff > 0:
                        arrow, color = '🔺', '#e53935'
                    else:
                        arrow, color = '🔻', '#2e7d32'
                    metric_rows += f'''<tr>
                        <td>{m['label']}</td>
                        <td>{m['yesterday']}{m['unit']}</td>
                        <td><strong style="color:{color};">{m['today']}{m['unit']}</strong></td>
                        <td>{arrow} {abs(diff):.1f}{m['unit']}</td></tr>'''
                metric_html = f'''<div style="margin-top:12px;">
                    <table style="width:100%;border-collapse:collapse;font-size:12px;">
                        <tr><th style="background:#7e57c2;padding:8px 10px;color:white;text-align:left;">指标</th>
                            <th style="background:#7e57c2;padding:8px 10px;color:white;text-align:left;">上次</th>
                            <th style="background:#7e57c2;padding:8px 10px;color:white;text-align:left;">本次</th>
                            <th style="background:#7e57c2;padding:8px 10px;color:white;text-align:left;">变化</th></tr>
                        {metric_rows}
                    </table></div>'''
            else:
                metric_html = '<p style="margin-top:12px;color:#bbb;font-size:13px;">📈 指标无变化</p>'

            # === 活动变化 ===
            activity_html = ''
            act = changes.get('activity_changes', {})
            if act and (act.get('added') or act.get('removed')):
                act_parts = []
                for a in act.get('added', []):
                    act_parts.append(f'<span style="display:inline-block;background:#e8f5e9;color:#2e7d32;padding:4px 12px;border-radius:16px;margin:2px;font-size:12px;">✅ + {a}</span>')
                for a in act.get('removed', []):
                    act_parts.append(f'<span style="display:inline-block;background:#ffebee;color:#e53935;padding:4px 12px;border-radius:16px;margin:2px;font-size:12px;">❌ - {a}</span>')
                activity_html = f'<div style="margin-top:12px;">{"".join(act_parts)}</div>'
            else:
                activity_html = '<p style="margin-top:12px;color:#bbb;font-size:13px;">🎯 活动无变化</p>'

            # === 组装竞品卡片 ===
            all_sections += f'''<div style="background:white;border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,0.06);border-left:4px solid {border};">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="font-size:20px;">{status_icon}</span>
                        <strong style="font-size:16px;color:#1F4E79;">{name}</strong>
                    </div>
                    <span style="color:#999;font-size:12px;">{changes['yesterday_time']} → {changes['today_time']}</span>
                </div>
                <p style="font-size:13px;color:#555;margin-bottom:4px;">{summary_text}</p>

                <div style="border-top:1px solid #f0f0f0;padding-top:12px;margin-top:8px;">
                    <div style="font-size:13px;font-weight:bold;color:#1F4E79;margin-bottom:4px;">💰 版本×周期价格变化</div>
                    {price_table_html}
                </div>

                <div style="border-top:1px solid #f0f0f0;padding-top:12px;margin-top:8px;">
                    <div style="font-size:13px;font-weight:bold;color:#1F4E79;margin-bottom:4px;">📈 核心指标变化</div>
                    {metric_html}
                </div>

                <div style="border-top:1px solid #f0f0f0;padding-top:12px;margin-top:8px;">
                    <div style="font-size:13px;font-weight:bold;color:#1F4E79;margin-bottom:4px;">🎯 活动变化</div>
                    {activity_html}
                </div>
            </div>'''

        html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日竞品变化监控</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Microsoft YaHei','PingFang SC',sans-serif; background:#f0f2f5; color:#333; padding:20px; }}
.container {{ max-width:960px; margin:0 auto; }}
h1 {{ text-align:center; color:#1F4E79; margin-bottom:6px; font-size:22px; }}
.subtitle {{ text-align:center; color:#999; font-size:13px; margin-bottom:20px; }}
.summary {{ background:white; border-radius:12px; padding:20px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.06); display:flex; justify-content:space-around; text-align:center; }}
.summary .num {{ font-size:32px; font-weight:bold; }}
.summary .label {{ font-size:13px; color:#888; margin-top:4px; }}
.summary .num.red {{ color:#e53935; }}
.summary .num.green {{ color:#2e7d32; }}
.summary .num.blue {{ color:#1F4E79; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ padding:8px 10px; text-align:left; font-size:12px; }}
td {{ padding:8px 10px; border-bottom:1px solid #f0f0f0; font-size:12px; }}
</style></head><body>
<div class="container">
<h1>📊 每日竞品变化监控</h1>
<p class="subtitle">{datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="summary">
    <div><div class="num blue">{len(urls)}</div><div class="label">监控竞品数</div></div>
    <div><div class="num red">{change_count}</div><div class="label">有变化</div></div>
    <div><div class="num green">{len(urls) - change_count - new_count}</div><div class="label">无变化</div></div>
    <div><div class="num" style="color:#42a5f5;">{new_count}</div><div class="label">🆕 首次采集</div></div>
</div>

{all_sections}

</div>
</body></html>'''
        return html
