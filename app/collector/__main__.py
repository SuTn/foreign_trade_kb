# app/collector/__main__.py
import asyncio
from app.collector.scanner import Scanner, write_status
from app.config import settings

async def main():
    write_status(settings.status_path, {"state": "starting"})
    # Playwright 启动 Chrome + 登录 (实际实现见 Task 9)
    # 此处为采集器入口骨架
    print("collector started (see Task 9 for Playwright bootstrap)")

if __name__ == "__main__":
    asyncio.run(main())
