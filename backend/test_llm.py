import logging
from llm_processor import LLMProcessor
from secret_store import SecretStore
import sys
import os

# 将 backend 目录添加到 sys.path 中以便导入模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# 配置基础日志方便观察输出
logging.basicConfig(level=logging.INFO)


def test_llm():
    print("--- 开始测试当前的 LLM 配置 ---")

    # 1. 加载配置
    store = SecretStore()
    config = store.load_config()

    if not config:
        print("❌ 错误：本地未找到 LLM 配置，请先在前端保存配置。")
        return

    print(f"✅ 成功读取配置。当前 Base URL: {config.get('base_url')}, Model: {config.get('model')}")

    # 2. 初始化 Processor
    processor = LLMProcessor(
        api_token=config.get("api_token"),
        base_url=config.get("base_url"),
        model=config.get("model"),
        timeout=config.get("timeout", 60),
        retry=config.get("retry", 3)
    )

    # 3. 发送一个简单的测试 Prompt
    test_prompt = "你好，请回答 1+1 等于几？如果收到请只回答数字 2。"
    print(f"💬 正在发送测试 Prompt: '{test_prompt}'")

    try:
        result = processor.generate(test_prompt)
        print("\n🎉 LLM 响应成功！")
        print(f"📝 返回内容: {result}")
    except Exception as e:
        print(f"\n❌ LLM 推理失败: {e}")


if __name__ == "__main__":
    test_llm()
