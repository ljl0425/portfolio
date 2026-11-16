# -*- coding: utf-8 -*-
"""本地演示启动脚本。

用法：
    set DEMO_MODE=1 && python start_demo.py          (Windows)
    DEMO_MODE=1 python start_demo.py                 (macOS/Linux)

打开 http://127.0.0.1:15001/
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(HERE, "exception-platform")
sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)

# 演示版强制开 DEMO_MODE，避免任何外部接口调用
os.environ.setdefault("DEMO_MODE", "1")
os.environ.setdefault("OPERATOR_PASSWORD", "1")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "15001"))
    print("=" * 56)
    print(" 产线异常处理平台 · 脱敏演示版")
    print(" DEMO_MODE =", os.environ["DEMO_MODE"])
    print(" 地址: http://127.0.0.1:%d/" % port)
    print(" 口令: %s" % os.environ["OPERATOR_PASSWORD"])
    print("=" * 56)
    from web_app import init_and_run

    init_and_run(port=port, start_services=False)
