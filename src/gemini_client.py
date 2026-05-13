"""
gemini_client.py
共享的 Gemini 生成客户端。
"""

import os
import time
from google import genai
from google.genai import errors as genai_errors
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = "gemini-2.5-pro"

client = genai.Client(api_key=GEMINI_API_KEY)


def call_gemini(prompt, max_retries=5):
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(model=MODEL, contents=prompt)
            return response.text.strip() if response.text else ""
        except Exception as e:
            e_str = str(e)
            if "429" in e_str or "503" in e_str or "RESOURCE_EXHAUSTED" in e_str or "UNAVAILABLE" in e_str:
                wait = 10 * (2 ** attempt)  # 10s, 20s, 40s, 80s, 160s
                print(f"  服务器过载，{attempt+1}/{max_retries}，等待 {wait}s...")
                time.sleep(wait)
            else:
                print(f"  API 错误: {e}")
                return ""
    print("  重试次数耗尽，跳过本次请求")
    return ""
