"""Copy to config.py and adjust local settings before starting the app."""

DATA_PATH = "."
MOSS_URL = "http://127.0.0.1:7862"
GRADIO_AUTH_USER = "admin"
GRADIO_AUTH_PASSWORD = None

PROMPTS = {
    "解释句意": "用中文解释这句英文的意思、语境和说话者意图，简洁准确。",
    "词汇与习语": "找出这句中的高价值词汇或习语，解释用法并给出两个自然例句。",
    "语法拆解": "拆解句子的语法结构，重点说明影响理解的结构，不逐词翻译。",
    "跟读练习": "给出这句的意群、重音和连读提示，并提供一轮简短跟读练习。",
    "情景对话": "围绕这句台词进行三轮自然英文对话，最后用中文给出简短反馈。",
}

AI_BASE_URL = None
AI_API_KEY = None
AI_MODEL = None
AI_API_MODE = "responses"  # Or "chat_completions" for older compatible services.
