from llm_processor_v2 import LLMProcessor


def test_candidate_urls_openai_root():
    p = LLMProcessor(api_token="t", base_url="https://api.openai.com", model="gpt-3.5-turbo")
    assert p._build_candidate_urls() == [
        "https://api.openai.com/v1/chat/completions",
        "https://api.openai.com/chat/completions",
    ]


def test_candidate_urls_with_v1():
    p = LLMProcessor(api_token="t", base_url="https://api.openai.com/v1", model="gpt-3.5-turbo")
    assert p._build_candidate_urls() == [
        "https://api.openai.com/v1/chat/completions",
        "https://api.openai.com/chat/completions",
    ]


def test_candidate_urls_full_endpoint():
    p = LLMProcessor(
        api_token="t",
        base_url="https://api.openai.com/v1/chat/completions",
        model="gpt-3.5-turbo",
    )
    assert p._build_candidate_urls() == [
        "https://api.openai.com/v1/chat/completions",
    ]

