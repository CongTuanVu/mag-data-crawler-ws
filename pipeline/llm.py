"""Lớp bọc Claude API: structured output + web search + vision.

Ba chế độ:
  mặc định   API Anthropic chính thức — structured output, prompt cache, server tool
  COMPAT     endpoint khác qua ANTHROPIC_BASE_URL (proxy nội bộ, LiteLLM, model local):
             ép JSON bằng prompt rồi tự parse, bỏ cache_control, không có server tool
  OFFLINE    giả lập hoàn toàn, không ra mạng (pipeline/mock.py)

Mọi lệnh gọi đều trả về dict đã hợp lệ theo JSON Schema — không regex hậu kỳ.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

import anthropic

from . import config

_client: Optional[anthropic.Anthropic] = None

WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 12},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 12},
]

FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)

WEB_TOOL_PREFIXES = ("web_search", "web_fetch")


def _is_web_tool(tool: Dict[str, Any]) -> bool:
    """Tool tra web của server — endpoint tương thích có thể ánh xạ sang CLI."""
    return any(str(tool.get("type", "")).startswith(p) for p in WEB_TOOL_PREFIXES)


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        kwargs: Dict[str, Any] = {"api_key": config.load_api_key(),
                                  "max_retries": 4, "timeout": 900.0}
        if config.BASE_URL:
            kwargs["base_url"] = config.BASE_URL
        _client = anthropic.Anthropic(**kwargs)
    return _client


def _text_of(msg) -> str:
    return "".join(b.text for b in msg.content if b.type == "text")


def _parse_json(text: str, label: str) -> Dict[str, Any]:
    """Bóc JSON từ text tự do (chế độ COMPAT — model không bị ép định dạng)."""
    for candidate in (text, *(m.group(1) for m in FENCE_RE.finditer(text))):
        try:
            return json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise RuntimeError(f"[{label}] không parse được JSON từ output "
                       f"({len(text)} ký tự): {text[:200]}")


def call_json(
    system: List[Dict[str, Any]],
    user_content: Any,
    schema: Dict[str, Any],
    tools: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = config.MAX_TOKENS,
    effort: str = config.EFFORT,
    label: str = "",
) -> Dict[str, Any]:
    """Gọi model, ép output theo `schema`, trả dict. Tự nối lại khi pause_turn."""
    if config.OFFLINE:
        from . import mock
        return mock.call_json(system, user_content, schema, tools, max_tokens, effort, label)

    system = list(system)
    kwargs: Dict[str, Any] = dict(model=config.MODEL, max_tokens=max_tokens)

    if config.COMPAT:
        if tools and not all(_is_web_tool(t) for t in tools):
            raise SystemExit(
                f"[{label}] Bước này cần server tool ngoài web_search/web_fetch — chỉ có ở\n"
                f"  API Anthropic chính thức, endpoint {config.BASE_URL} không cung cấp.")
        if tools:
            # Endpoint tương thích có thể ánh xạ web_search/web_fetch sang tool
            # WebSearch/WebFetch sẵn có của CLI — cứ chuyển tiếp để nó tự quyết.
            kwargs["tools"] = tools
        system = [{k: v for k, v in b.items() if k != "cache_control"} for b in system]
        system.append({"type": "text", "text":
                       "Chỉ trả về DUY NHẤT một object JSON hợp lệ, không kèm giải thích, "
                       "không bọc trong ```. JSON phải khớp schema sau:\n"
                       + json.dumps(schema, ensure_ascii=False)})
    else:
        kwargs["output_config"] = {"effort": effort,
                                   "format": {"type": "json_schema", "schema": schema}}
        if tools:
            kwargs["tools"] = tools
    kwargs["system"] = system

    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_content}]
    msg = None
    for _ in range(6):                             # trần chống lặp vô hạn pause_turn
        with client().messages.stream(messages=messages, **kwargs) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"[{label}] model từ chối: {getattr(msg, 'stop_details', None)}")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError(f"[{label}] chạm max_tokens={max_tokens}, tăng WS1_MAX_TOKENS")
        if msg.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": msg.content}]
        time.sleep(1)
    else:
        raise RuntimeError(f"[{label}] pause_turn quá 6 vòng")

    u = msg.usage
    print(f"    · {label}: in={u.input_tokens:,} "
          f"cache_r={getattr(u, 'cache_read_input_tokens', 0) or 0:,} out={u.output_tokens:,}")
    text = _text_of(msg)
    return _parse_json(text, label) if config.COMPAT else json.loads(text)


def cached(text: str) -> Dict[str, Any]:
    """Block system có cache breakpoint — dùng cho corpus raw dùng lại nhiều lần."""
    block: Dict[str, Any] = {"type": "text", "text": text}
    if not config.COMPAT:
        block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    return block


def image_block(data_b64: str, media_type: str) -> Dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data_b64}}


def call_text(
    system: List[Dict[str, Any]],
    user_content: Any,
    max_tokens: int = config.MAX_TOKENS,
    effort: str = config.EFFORT,
    label: str = "",
) -> str:
    """Gọi model lấy TEXT thô, không ép JSON schema.

    Dùng cho bước sinh code (pipeline/codegen.py): mã nguồn nhét trong chuỗi JSON
    vừa tốn token vừa dễ hỏng vì escape, nên ở đó ta phân định file bằng dòng
    `===== FILE: … =====` rồi tự cắt.
    """
    if config.OFFLINE:
        from . import mock
        return mock.call_text(system, user_content, max_tokens, effort, label)

    kwargs: Dict[str, Any] = dict(model=config.MODEL, max_tokens=max_tokens)
    if config.COMPAT:
        system = [{k: v for k, v in b.items() if k != "cache_control"} for b in system]
    else:
        kwargs["output_config"] = {"effort": effort}
    kwargs["system"] = system

    with client().messages.stream(
            messages=[{"role": "user", "content": user_content}], **kwargs) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"[{label}] model từ chối: {getattr(msg, 'stop_details', None)}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError(f"[{label}] chạm max_tokens={max_tokens} — tăng WS1_CODEGEN_MAX_TOKENS")
    u = msg.usage
    print(f"    · {label}: in={u.input_tokens:,} "
          f"cache_r={getattr(u, 'cache_read_input_tokens', 0) or 0:,} out={u.output_tokens:,}")
    return _text_of(msg)
