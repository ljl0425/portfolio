# 李嘉乐 · 作品集

半导体显示制造一线的 AI 落地案例。三个案例，每个都讲三件事：**以前怎么做、现在怎么做、关键决策与取舍**。

## 页面

- `index.html` — 首页（三个决定的索引 + 判断方法）
- `case-1-platform.html` — 产线异常处理平台（30 分钟 → 3 分钟）
- `case-2-agent.html` — 异常单处理 Agent（值班 4 人 → 2 人）
- `case-3-rpa.html` — RPA 自动复机（人不到场，机台自己恢复）

## 脱敏说明

真实系统在产线运行。本仓库里的机台编号、内部系统名、业务字段与地址全部替换为 `DEMO-*` 占位，数据由脚本生成；`demo/` 是可以本地跑起来的脱敏副本。

## 本地跑 Demo

```bash
cd demo
pip install -r requirements.txt
python seed_demo_data.py      # 生成演示数据
python start_demo.py          # 打开 http://127.0.0.1:15001  密码 1
```

演示模式只走量测（OVL）与缺陷两条链路；制程参数（CD）链路涉及真实接口，已在演示模式关闭。

## 技术栈

Python / Flask / SQLite / Matplotlib / OpenCV

---

个人作品集 · 案例已脱敏
