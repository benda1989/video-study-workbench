from __future__ import annotations

import httpx

from config import AI_API_KEY, AI_API_MODE, AI_BASE_URL, AI_MODEL, PROMPTS


def response_text(data: dict) -> str:
    if data.get("status") in {"failed", "incomplete"} or data.get("error"):
        raise RuntimeError(f"AI 响应未完成：{data.get('error') or data.get('incomplete_details') or data.get('status')}")
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"].strip()
    outputs = data.get("output")
    if not isinstance(outputs, list):
        raise RuntimeError("AI 响应格式不正确")
    parts = []
    for output in outputs:
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        contents = output.get("content")
        if not isinstance(contents, list):
            continue
        for content in contents:
            if isinstance(content, dict) and content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    answer = "\n".join(parts).strip()
    if not answer:
        raise RuntimeError("AI 未返回文本内容")
    return answer


class AIClient:
    def ask(self, title: str, sentence: str, question: str, template: str, history: list[dict]) -> str:
        if not (AI_BASE_URL and AI_MODEL):
            raise RuntimeError("AI 未配置：请设置 AI_BASE_URL 和 AI_MODEL")
        if not question.strip() and template not in PROMPTS:
            raise ValueError("请输入问题或选择提问模板")
        system = "你是一位英语学习陪练。基于提供的台词回答，不编造未给出的剧情；回答清楚、具体、简短。"
        context = f"视频：{title}\n当前台词：{sentence or '未选择'}"
        prompt = f"{PROMPTS.get(template, '')}\n{question.strip()}".strip()
        messages = [{"role": "user", "content": context}]
        messages.extend({"role": item["role"], "content": item["content"]} for item in history[-12:] if item.get("role") in {"user", "assistant"})
        messages.append({"role": "user", "content": prompt})
        mode = AI_API_MODE or "responses"
        if mode == "responses":
            endpoint = "responses"
            payload = {"model": AI_MODEL, "instructions": system, "input": messages}
        elif mode == "chat_completions":
            endpoint = "chat/completions"
            payload = {"model": AI_MODEL, "messages": [{"role": "system", "content": system}, *messages], "temperature": 0.4}
        else:
            raise ValueError("不支持的 AI_API_MODE")
        headers = {"Authorization": f"Bearer {AI_API_KEY}"} if AI_API_KEY else {}
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(f"{str(AI_BASE_URL).rstrip('/')}/{endpoint}", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError(f"AI 请求失败：{error}") from error
        if not isinstance(data, dict):
            raise RuntimeError("AI 响应格式不正确")
        if mode == "responses":
            return response_text(data)
        try:
            answer = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise RuntimeError("AI 响应格式不正确") from error
        if not answer:
            raise RuntimeError("AI 未返回内容")
        return answer
