"""启动演示服务器（DEMO_MODE，端口 15001，独立演示库）。

用法：python run_demo_server.py

DEMO_MODE 在本进程内设置，避免 PowerShell 子进程不继承环境变量的问题。
"""
import os

os.environ["DEMO_MODE"] = "1"
os.environ["OPERATOR_PASSWORD"] = "1"

from web_app import init_and_run

if __name__ == "__main__":
    init_and_run(port=15001, start_services=False)
