# app/__main__.py
"""启动脚本: 同时拉起 Web 进程 + 采集器进程。"""
import subprocess, sys, os, signal

def main():
    # 采集器进程
    collector = subprocess.Popen([sys.executable, "-m", "app.collector"])
    # Web 进程 (主进程)
    try:
        import uvicorn
        uvicorn.run("app.web.app:create_app", factory=True, host="127.0.0.1", port=8000)
    finally:
        collector.terminate()
        collector.wait()

if __name__ == "__main__":
    main()
