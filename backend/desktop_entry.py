import argparse
import os
import uvicorn


def main() -> None:
    """
    桌面端打包后端入口。

    说明：
    - 该入口用于 PyInstaller 打包成单文件二进制（ai-dcp-backend）。
    - 需要显式 import FastAPI 的 app 对象，避免仅用字符串 "main:app" 导致打包时漏收 main.py。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AI_DCP_PORT", "8002")))
    args = parser.parse_args()

    from main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
