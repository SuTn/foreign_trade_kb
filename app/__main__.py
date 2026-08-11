# app/__main__.py
"""启动脚本: supervisor 守护采集器子进程 (非 0 退出等 3s 重启), 主进程跑 Web。"""
import subprocess, sys, time, threading, os, signal

_active = None
_started = threading.Event()

def _supervise():
    """守护采集器: rc==0 正常退出不重启; 非 0 等 3s 重启。"""
    global _active
    while True:
        proc = subprocess.Popen([sys.executable, "-m", "app.collector"])
        _active = proc
        _started.set()
        rc = proc.wait()
        if rc == 0:
            break
        print(f"[supervisor] collector exited rc={rc}, restarting in 3s...", flush=True)
        time.sleep(3)

def _terminate_process_group(proc):
    """终止采集器进程树: Windows 用 taskkill /T, POSIX 用 killpg。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait()
    except Exception:
        pass

def main():
    threading.Thread(target=_supervise, daemon=True).start()
    _started.wait(10)  # 采集器启动后再进 uvicorn (阻塞入口)
    try:
        import uvicorn
        uvicorn.run("app.web.app:create_app", factory=True, host="127.0.0.1", port=8000)
    except KeyboardInterrupt:
        pass
    finally:
        _terminate_process_group(_active)

if __name__ == "__main__":
    main()
