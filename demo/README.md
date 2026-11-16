# 产线异常处理平台 · 可运行 Demo（脱敏版）

半导体黄光（曝光）产线的异常处理平台原型。日常场景：产线某台设备参数异常 / 量测超规 / 缺陷超标，
值班人员需要拉数据、判断范围、决定是否 Hold 货、出一份可追溯的处理报告。这个平台把这条链路搬到了网页上。

**这是脱敏演示版**：机台名、厂区、产品、内部系统接口全部替换为 `DEMO-*` 占位，数据是脚本生成的假数据，
不连接任何真实产线系统。原版部署在产线内网，本仓库只保留可独立运行的骨架与演示模式。

---

## 目录结构

```
demo/
├─ exception-platform/      Flask 主服务：任务管理 + 三类异常报告引擎 + 日常监控 + THK 缺陷检测
│  ├─ web_app.py            路由入口（提交异常 / 任务列表 / 报告页 / 配置）
│  ├─ database.py           SQLite 存取（演示模式下自动切 *_demo.db）
│  ├─ process_engine.py     制程参数异常报告
│  ├─ report_engine.py      量测异常（OVL）报告
│  ├─ defect_engine.py      缺陷（Defect）异常报告
│  ├─ cd_engine.py          量测异常（CD）报告 —— 演示版未提供假数据，默认跳过
│  ├─ monitor_routes.py     日常监控（Dose / OVL / ECPM / 工事 / Hold 货）
│  ├─ yield_chart_renderer.py  Matplotlib 出图
│  ├─ thk/                  缺陷图像检测（OpenCV）
│  └─ seed_demo_data.py     一键灌演示数据
└─ monitor-platform/        定时监控：轮询 → 生成报告 → 推送到飞书

start_demo.py               本地演示启动脚本
```

## 跑起来（Windows / macOS / Linux 均可）

```bash
pip install -r requirements.txt

# 1) 灌演示数据（只需一次）
set DEMO_MODE=1            # macOS/Linux: export DEMO_MODE=1
python exception-platform/seed_demo_data.py

# 2) 起服务
set DEMO_MODE=1
python start_demo.py
# 打开 http://127.0.0.1:15001/
```

演示模式（`DEMO_MODE=1`）下所有外部接口调用被短路，直接用内置假数据生成报告，
所以**断网也能跑通全流程**。

## 演示能做什么

1. 首页提交一条异常（选站点 / 机台 / 主制程 / 异常类型 / 时间范围）
2. 打开任务 → 生成报告 → 看到统计卡、异常清单表、分布柱状图、量测值折线图
3. 在日常监控页触发 Dose / OVL / ECPM 等日度报告
4. 报告可导出 URL 存档，值班人员按报告决定是否 Hold 货

## 已知边界

- `cd_engine.py`（CD 维度）**没有演示分支**，需要产线 EDC 接口，公开版直接跳过，请用 OVL 或 Defect 两个任务体验。
- THK 图像检测需要自己放样本图到 `exception-platform/thk/`，仓库不带真实缺陷图。
- 原版的自动复机（RPA）看板依赖内网心跳目录与告警接口，属于另一套系统，未包含在本仓库。

## 技术栈

Python 3.10+ / Flask / SQLite / Matplotlib / OpenCV / openpyxl。
