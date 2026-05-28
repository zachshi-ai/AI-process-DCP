import argparse
import uvicorn


def main() -> None:
    """
    直接以对象方式启动 WebUI 版后端（避免字符串形式 "module:app" 在打包时的隐式导入问题）。

    使用方式（示例）：
    - ai-dcp-backend-webui.exe --host 127.0.0.1 --port 8000
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    from main_webui import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

