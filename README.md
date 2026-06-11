# 京东服务市场竞品监控 Skill

这是一个适配京东服务市场 `fw.jd.com` 的通用竞品监控 Skill。它可以通过浏览器 CDP 自动采集服务商品详情页中的价格矩阵、评分、评价、已买人数、续订率、服务商信息和活动标签，并将历史数据保存到 SQLite，生成单品报告、批量对比报告和每日变化监控报告。

本版本用于作品集展示，不包含任何内部资料、私有商品名单、真实历史数据库或业务专属口径。使用者需要自行配置监控链接和运行环境。

## 目录结构

```text
jd-service-market-monitor-skill/
├── SKILL.md                         # 给 Codex / AI Agent 读取的 Skill 说明
├── README.md                        # 给使用者阅读的配置和使用说明
├── run.py                           # 命令行入口
├── competitor_agent.py              # CDP 采集、SQLite 存储、变化检测、报告生成核心逻辑
├── competitor_agent_templates.py    # HTML 报告模板
└── schedule.sh                      # Linux/macOS 定时任务脚本
```

运行后会自动生成：

```text
competitor_data.db                   # SQLite 历史数据库
每日竞品变化监控.html
竞品报告_<商品名称>.html
竞品对比报告.html
logs/
```

这些运行产物不建议提交到公开作品集中。

## 环境要求

- Python 3.10+
- Microsoft Edge 或 Chrome
- Python 依赖：

```bash
pip install sqlalchemy websockets
```

## 启动浏览器 CDP

采集依赖浏览器调试端口 `9222`。Windows 可用：

```powershell
$profile="$env:TEMP\jd-service-market-monitor"
New-Item -ItemType Directory -Force -Path $profile | Out-Null
Start-Process -FilePath "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  -ArgumentList @("--remote-debugging-port=9222", "--user-data-dir=$profile", "--no-first-run", "about:blank")
```

macOS / Linux 可参考：

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/jd-service-market-monitor about:blank
```

如果使用 Edge，把命令中的 `google-chrome` 替换为本机 Edge 可执行文件路径。

## 首次配置

1. 将本文件夹放到 Codex Skills 目录或作为独立脚本目录使用。
2. 启动带 CDP 端口的浏览器。
3. 准备需要监控的京东服务市场商品链接，格式通常为：

```text
https://fw.jd.com/main/detail/FW_GOODS-xxxx
https://fw.jd.com/market/new/detail/FW_GOODS-xxxx
```

4. 逐个添加并采集：

```bash
python run.py 采集 "https://fw.jd.com/main/detail/FW_GOODS-xxxx"
```

5. 添加完成后可执行批量采集或每日执行：

```bash
python run.py 每日执行
```

## 常用命令

```bash
python run.py 采集 "https://fw.jd.com/main/detail/FW_GOODS-xxxx"
python run.py 批量采集
python run.py 列表
python run.py 报告 <商品名称>
python run.py 对比
python run.py 每日报告
python run.py 每日执行
```

指定报告输出目录：

```bash
COMPETITOR_MONITOR_OUTPUT_DIR=/path/to/output python run.py 每日执行
```

PowerShell：

```powershell
$env:COMPETITOR_MONITOR_OUTPUT_DIR="D:\reports\jd-monitor"
python .\run.py 每日执行
```

## README 配置建议

如果你把这个 Skill 交给其他人或放进自己的项目，请在 README 中替换或补充以下内容：

| 配置项 | 应填写内容 |
|------|------|
| 项目用途 | 说明监控对象是京东服务市场的哪些服务商品，不要写内部代号 |
| 安装路径 | Skill 文件夹放置位置，或独立脚本目录 |
| Python 环境 | Python 版本、虚拟环境路径、依赖安装命令 |
| 浏览器路径 | 本机 Edge/Chrome 可执行文件路径 |
| CDP 端口 | 默认 `9222`，如需修改需同步调整代码 |
| CDP 页面列表 | 默认 `http://127.0.0.1:9222/json/list`，也可通过 `JD_MONITOR_CDP_LIST_URL` 覆盖 |
| 监控 URL | 使用者自己的 `fw.jd.com` 商品详情页链接 |
| 输出目录 | HTML 报告和日志保存位置 |
| 定时规则 | 是否每天执行、执行时间、日志文件位置 |
| 数据合规 | 是否允许保存商品历史数据、是否需要清理数据库后再分享 |

公开展示时建议保留通用描述，不要包含真实客户、内部系统、私有商品清单、账号信息、Cookie、数据库文件或历史报告。

## 每日报告对比逻辑

每日报告使用今天最新的有效记录作为“本次”，并与今天之前最近一个有记录日期的最新有效记录对比。同一天内多次补采只用于修正当天数据，不会作为“上次”基准。

有效记录优先满足：

- 商品名称不是泛化页面标题。
- 价格矩阵不为空。
- 至少存在一个可用价格。

## 空壳重试机制

京东服务市场详情页是前端异步渲染，页面刚加载完成时可能只有通用页面壳。脚本会识别以下异常：

- 标题为“服务详情-京麦服务市场”“京麦服务市场”“服务详情”。
- 价格矩阵为空。
- 有效价格为 0。

遇到异常时，脚本会在单个商品内等待并重试，最多 3 次；3 次仍失败才继续下一个商品。批量采集结束后建议再做一次数据库质量复查。

## 定时任务

Linux/macOS：

```bash
bash schedule.sh install
bash schedule.sh status
bash schedule.sh run
bash schedule.sh uninstall
```

Windows 可使用任务计划程序，动作配置为：

```text
python run.py 每日执行
```

并在任务环境中设置 `COMPETITOR_MONITOR_OUTPUT_DIR`。

## 说明

这个版本展示了：

- 基于 CDP 的动态网页采集。
- 复杂价格矩阵解析。
- SQLite 历史数据沉淀。
- 日级变化检测。
- 页面空壳质量检查和重试策略。
- 中文 HTML 报告生成。
- 可定时执行的批量监控流程。
