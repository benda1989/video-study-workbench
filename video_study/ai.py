import json
from collections.abc import Iterator
from itertools import chain

import httpx

import config


def _text(data: dict, responses: bool) -> str:
    if data.get("error") or data.get("status") not in (None, "completed"):
        raise RuntimeError("AI 生成失败或输出不完整")
    if responses:
        return data.get("output_text") or "\n".join(
            part["text"] for item in data.get("output", [])
            for part in item.get("content", []) if part.get("type") == "output_text"
        )
    choice = data["choices"][0]
    if choice.get("finish_reason") not in (None, "stop") or choice["message"].get("refusal"):
        raise RuntimeError("AI 未正常完成请求")
    return choice["message"].get("content") or ""


def _events(response: httpx.Response) -> Iterator[dict | None]:
    data, name = [], ""
    for line in chain(response.iter_lines(), [""]):
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif not line:
            if data:
                raw = "\n".join(data)
                if raw.strip() == "[DONE]":
                    yield None
                    return
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError("Invalid SSE event")
                event.setdefault("type", name)
                yield event
            data, name = [], ""


def _stream(response: httpx.Response, responses: bool) -> str:
    parts, stopped = {}, False
    for event in _events(response):
        if event is None:
            if not responses and stopped:
                return "".join(parts.get(0, []))
            break
        kind = event.get("type")
        if event.get("error") or kind in ("error", "response.failed", "response.incomplete", "response.cancelled", "response.refusal.delta", "response.refusal.done"):
            raise RuntimeError("AI 生成失败、被拒绝或输出不完整")
        if responses:
            if kind in ("response.output_text.delta", "response.output_text.done"):
                key = (event.get("output_index", 0), event.get("content_index", 0))
                if kind.endswith(".delta"):
                    parts.setdefault(key, []).append(event["delta"])
                else:
                    parts[key] = [event["text"]]
            elif kind == "response.completed":
                return _text(event["response"], True) or "".join("".join(parts[key]) for key in sorted(parts))
        else:
            for choice in event["choices"]:
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                if delta.get("refusal") or choice.get("finish_reason") not in (None, "stop"):
                    raise RuntimeError("AI 未正常完成请求")
                parts.setdefault(0, []).append(delta.get("content") or "")
                stopped = stopped or choice.get("finish_reason") == "stop"
    raise RuntimeError("AI 流提前结束，未保存不完整结果")


class AIClient:
    def ask(self, title: str, sentence: str, question: str, template: str, history: list[dict]) -> str:
        if not question.strip() and template not in config.PROMPTS:
            raise ValueError("请输入问题或选择提问模板")
        messages = [{"role": "user", "content": f"视频：{title}\n当前台词：{sentence or '未选择'}"}]
        messages.extend({"role": item["role"], "content": item["content"]} for item in history[-12:] if item.get("role") in {"user", "assistant"})
        messages.append({"role": "user", "content": f"{config.PROMPTS.get(template, '')}\n{question.strip()}".strip()})
        return self.complete("你是一位英语学习陪练。基于台词回答，不编造剧情；回答清楚、具体、简短。", messages)

    def complete(self, system: str, messages: list[dict]) -> str:
        if not (config.AI_BASE_URL and config.AI_MODEL):
            raise RuntimeError("AI 未配置：请设置 AI_BASE_URL 和 AI_MODEL")
        responses = config.AI_API_MODE == "responses"
        endpoint = "responses" if responses else "chat/completions"
        payload = {"model": config.AI_MODEL, "stream": True}
        if responses:
            payload.update(instructions=system, input=messages)
        else:
            payload.update(messages=[{"role": "system", "content": system}, *messages], temperature=0.4)
        headers = {"Accept": "text/event-stream"}
        if config.AI_API_KEY:
            headers["Authorization"] = f"Bearer {config.AI_API_KEY}"
        try:
            timeout = httpx.Timeout(getattr(config, "AI_READ_TIMEOUT", 180), connect=getattr(config, "AI_CONNECT_TIMEOUT", 15))
            with httpx.Client(timeout=timeout, proxy=getattr(config, "AI_PROXY", "") or None) as client:
                with client.stream("POST", f"{config.AI_BASE_URL.rstrip('/')}/{endpoint}", headers=headers, json=payload) as response:
                    response.raise_for_status()
                    if "text/event-stream" in response.headers.get("content-type", "").lower():
                        response.encoding = "utf-8"
                        answer = _stream(response, responses)
                    else:
                        response.read()
                        answer = _text(response.json(), responses)
                    if not answer.strip():
                        raise RuntimeError("AI 未返回文本内容")
                    return answer.strip()
        except httpx.ConnectTimeout as error:
            raise RuntimeError("连接 AI 服务超时，请检查接口地址、网络或代理") from error
        except httpx.TimeoutException as error:
            raise RuntimeError("等待 AI 响应超时，请重试或调整 AI_READ_TIMEOUT") from error
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f"AI 请求失败：HTTP {error.response.status_code}") from error
        except httpx.HTTPError as error:
            raise RuntimeError("AI 连接失败或中断，请重试") from error
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
            raise RuntimeError("AI 配置或响应格式不正确") from error
