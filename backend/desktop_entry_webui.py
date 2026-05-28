import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    uvicorn.run("main_webui:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

