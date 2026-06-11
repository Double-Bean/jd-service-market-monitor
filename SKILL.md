---
name: jd-service-market-monitor
description: 适配京东服务市场 fw.jd.com 的通用竞品监控 Skill。用于采集服务商品详情页的价格矩阵、评分、评价、已买人数、续订率、服务商信息和活动标签，沉淀 SQLite 历史数据，生成单品报告、批量对比报告和每日变化监控报告。适用于用户要求监控京东平台服务商品、批量采集竞品、生成日报、检测价格或指标变化、处理页面空壳重试等场景。
---
# 京东服务市场竞品监控 Skill

## 适用场景

当用户提供 `fw.jd.com` 的京东服务市场商品链接，或要求采集、监控、对比、生成日报时使用本 Skill。

本 Skill 是通用版，不绑定任何内部资料、业务线、私有商品名单或专属口径。首次使用时需要由使用者自己添加需要监控的京东服务市场商品链接。

## 核心能力

| 功能 | 说明 |
|------|------|
| 单品采集 | 采集京东服务市场商品名称、评分、评价、已买人数、续订率、服务商、活动和价格矩阵 |
| 批量采集 | 对已入库 URL 执行批量更新 |
| 每日报告 | 生成所有已监控商品的当天概览和变化报告 |
| 变化检测 | 对比今天最新有效记录与今天之前最近一个有记录日期的最新有效记录 |
| 空壳重试 | 遇到泛化标题、价格矩阵为空、有效价格为 0 时，在单品内等待并最多重试 3 次 |
| 定时执行 | 可通过 `schedule.sh` 配置每天固定时间执行 |

## 运行前提

1. Python 3.10+。
2. Python 依赖：`sqlalchemy`、`websockets`。
3. 本机浏览器开启 Chrome DevTools Protocol，默认端口为 `9222`。
4. 采集目标必须是京东服务市场详情页，例如：

```text
https://fw.jd.com/main/detail/FW_GOODS-xxxx
https://fw.jd.com/market/new/detail/FW_GOODS-xxxx
```

## 浏览器采集通道

采集依赖 CDP 连接 `http://127.0.0.1:9222`。如果端口未开启，可参考以下命令启动 Edge 或 Chrome：

```powershell
$profile="$env:TEMP\jd-service-market-monitor"
New-Item -ItemType Directory -Force -Path $profile | Out-Null
Start-Process -FilePath "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  -ArgumentList @("--remote-debugging-port=9222", "--user-data-dir=$profile", "--no-first-run", "about:blank")
```

连接页面列表时，要优先选择 `type=page` 且不是 `chrome-extension://` 的真实网页标签页。

## 命令

在 Skill 目录下执行：

```bash
python run.py 采集 "https://fw.jd.com/main/detail/FW_GOODS-xxxx"  # 添加并采集单个商品
python run.py 批量采集                                             # 采集所有已入库商品
python run.py 列表                                                 # 查看最新记录
python run.py 报告 <商品名称>                                      # 生成单个商品报告
python run.py 对比                                                 # 生成多商品对比报告
python run.py 每日报告                                             # 生成每日监控报告
python run.py 每日执行                                             # 批量采集 + 生成每日报告
```

也可以指定报告输出目录：

```bash
COMPETITOR_MONITOR_OUTPUT_DIR=/path/to/output python run.py 每日执行
```

如浏览器 CDP 页面列表不使用默认地址，可设置：

```bash
JD_MONITOR_CDP_LIST_URL=http://127.0.0.1:9222/json/list python run.py 每日执行
```

Windows PowerShell：

```powershell
$env:COMPETITOR_MONITOR_OUTPUT_DIR="D:\reports\jd-monitor"
python .\run.py 每日执行
```

## 数据和报告

默认情况下：

- SQLite 数据库：`competitor_data.db`，自动创建在 Skill 目录下。
- 每日报告：`每日竞品变化监控.html`。
- 单品报告：`竞品报告_<商品名称>.html`。
- 对比报告：`竞品对比报告.html`。

作品集或公开展示时，不应提交真实业务数据库、历史采集结果、私有商品清单或内部报告。

## 采集字段

每次采集会尽量获取：

- 商品名称
- 综合评分和子评分
- 好评率、评价数、好评/中评/差评数量
- 已买人数
- 续订率
- 版本列表和周期选项
- 版本 × 周期价格矩阵
- 原价、促销价、免费试用、不可用状态
- 服务商名称、电话、区域
- 活动标签

## 变化检测规则

每日报告不是简单对比“上一条记录”，而是：

1. 取今天最新的有效记录作为“本次”。
2. 取今天之前最近一个有记录日期的最新有效记录作为“上次”。
3. 同一天内多次补采只用于修正当天数据，不作为历史对比基准。
4. 有效记录优先选择非泛化标题、价格矩阵非空、至少有一个可用价格的记录。

## 空壳和重试规则

京东服务市场详情页为前端异步渲染，导航完成时可能只有页面壳。常见表现：

- 标题为“服务详情-京麦服务市场”“京麦服务市场”“服务详情”。
- 价格矩阵为空。
- 没有任何可用价格。

处理规则：

1. 单个商品采集完成后先做质量检查。
2. 若疑似空壳，等待后在该商品内重试。
3. 最多重试 3 次。
4. 3 次仍失败才保留最后一次结果并继续下一个 URL。
5. 批量采集后仍建议做最终质量复查；若发现异常，只补采异常 URL 并重新生成每日报告。

## 定时任务

Linux/macOS 可使用：

```bash
bash schedule.sh install
bash schedule.sh status
bash schedule.sh run
bash schedule.sh uninstall
```

定时任务执行时仍需要机器上可访问 CDP 浏览器端口。

## 使用者需要配置的内容

交付或安装给新用户时，请在 README 中明确写清：

1. Skill 放置路径或安装方式。
2. Python 版本和依赖安装命令。
3. 如何启动带 `--remote-debugging-port=9222` 的浏览器。
4. 首次添加哪些 `fw.jd.com` 商品 URL。
5. 报告输出目录如何设置。
6. 是否启用定时任务以及执行时间。
7. 不要提交真实数据库和私有监控名单。

## 注意事项

1. 页面结构变化可能导致选择器失效，需要按实际页面维护采集逻辑。
2. 京东平台可能存在访问频率、登录态、地区或账号差异，采集结果应以当时可见页面为准。
3. 免费试用周期、优惠标签和不可用状态会动态变化，应以价格矩阵中的每个组合为准。
4. `disabled` 组合标记为不可用，不计入有效价格。
5. Windows 控制台建议设置 UTF-8，避免 `¥` 等字符输出异常。
