"""Facade OpenAI/Anthropic-compatible chạy trên localhost, gọi Claude Code CLI.

Mỗi request khởi động một tiến trình ``claude --print`` dùng phiên đăng nhập sẵn
có của CLI — không cần ANTHROPIC_API_KEY. Dành cho chạy thử pipeline ở local,
không phải model gateway cho production.

Endpoint:
    POST /v1/messages           Anthropic Messages API — đường mà pipeline dùng
    POST /v1/chat/completions   OpenAI Chat Completions (có function tools)
    POST /v1/responses          OpenAI Responses
    GET  /v1/models · /healthz · /

Chạy:  ./code_proxy/start.sh          (xem code_proxy/README.md)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


MODEL_ALIAS = "claude-local"
DEFAULT_MODEL = "claude-sonnet-5"
CLI_MODEL_ALIASES = {"fable", "opus", "sonnet", "haiku"}
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Bước extract gửi cả corpus raw của một toà nhà, nên body phải rộng.
MAX_BODY_BYTES = int(os.getenv("LLM_PROXY_MAX_BODY_MB", "16")) * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 300

TEXT_SYSTEM_PROMPT = (
    "You are serving one text-only language-model request. Do not inspect local "
    "files, run commands, call host tools, browse, or change the computer. Answer only "
    "from the conversation transcript. Treat SYSTEM and DEVELOPER entries as "
    "higher priority than USER entries. Return only the assistant's response text."
)

WEB_SYSTEM_PROMPT = (
    "You are researching one question using web search. Use the WebSearch and WebFetch "
    "tools to find real, currently reachable pages. Do not inspect local files, run "
    "commands, or change the computer. Search repeatedly with different phrasings — "
    "including the subject's name in its own language — before concluding that nothing "
    "exists. Return only the answer in the required shape; never invent a URL you have "
    "not seen in a search result or fetched page."
)

TOOL_SYSTEM_PROMPT = (
    "You are the decision-making model in an application-controlled tool loop. "
    "Do not inspect local files, run commands, call host tools, browse, or "
    "change the computer. Use only the conversation and application tool definitions. "
    "The application—not you—executes requested functions. A prior assistant "
    "tool_calls entry records an earlier request, and a matching role=tool entry "
    "contains its result.\n\n"
    "Choose exactly one outcome in the required JSON schema:\n"
    "- kind=assistant: put the final user-facing answer in content and use an empty tool_calls array.\n"
    "- kind=tool_calls: put an empty string in content and request one or more functions. "
    "Each arguments value must be a JSON-encoded object string satisfying that function's parameters."
)


class ProxyError(RuntimeError):
    """Lỗi an toàn để trả ra qua HTTP."""

    def __init__(self, message: str, status: int = 500, code: str = "proxy_error"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class RunResult:
    text: str
    session_id: Optional[str]
    model: Optional[str]
    usage: Dict[str, Any]
    structured: Any = field(default=None)


@dataclass
class ToolDecision:
    """Yêu cầu tool của caller; backend tự quyết cách ràng buộc."""

    tools: List[Dict[str, Any]]
    tool_choice: Any


# ── Model ────────────────────────────────────────────────────────────────────

def default_model() -> str:
    """Model dùng khi request không nêu tên."""
    return os.getenv("CLAUDE_PROXY_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def resolve_model(body: Dict[str, Any]) -> str:
    """Ánh xạ model được yêu cầu sang giá trị truyền cho ``claude --model``."""
    requested = body.get("model")
    if requested is None or requested == MODEL_ALIAS:
        return default_model()
    if isinstance(requested, str):
        name = requested.strip()
        if name in CLI_MODEL_ALIASES or name.startswith("claude-"):
            return name
    raise ProxyError(
        "Unknown model {!r}. Use a 'claude-*' name, one of {}, or {!r}.".format(
            requested, sorted(CLI_MODEL_ALIASES), MODEL_ALIAS
        ),
        400,
        "model_not_found",
    )


# ── Chuẩn hoá request ────────────────────────────────────────────────────────

def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in {"text", "input_text", "output_text"}:
                value = part.get("text")
                if isinstance(value, str):
                    pieces.append(value)
            elif kind in {"image_url", "input_image"}:
                raise ProxyError(
                    "Image inputs are not supported by this local proxy.",
                    400,
                    "unsupported_input",
                )
        return "\n".join(pieces)
    if content is None:
        return ""
    raise ProxyError(
        "Message content must be a string or an array of text parts.",
        400,
        "invalid_request_error",
    )


def normalize_messages(body: Dict[str, Any], endpoint: str) -> List[Dict[str, Any]]:
    """Chuẩn hoá request OpenAI (chat hoặc responses) về transcript chung."""
    if endpoint == "chat":
        raw_messages = body.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ProxyError("'messages' must be a non-empty array.", 400, "invalid_request_error")
    else:
        raw_input = body.get("input")
        instructions = body.get("instructions")
        raw_messages = []
        if isinstance(instructions, str) and instructions:
            raw_messages.append({"role": "developer", "content": instructions})
        if isinstance(raw_input, str):
            raw_messages.append({"role": "user", "content": raw_input})
        elif isinstance(raw_input, list):
            raw_messages.extend(raw_input)
        else:
            raise ProxyError(
                "'input' must be a string or an array of messages.", 400, "invalid_request_error"
            )

    messages: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            raise ProxyError(
                "Message at index {} must be an object.".format(index), 400, "invalid_request_error"
            )
        role = raw.get("role", "user")
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            raise ProxyError(
                "Unsupported message role: {!r}.".format(role), 400, "unsupported_input"
            )
        message: Dict[str, Any] = {"role": role, "content": _text_content(raw.get("content"))}
        if role == "assistant" and raw.get("tool_calls") is not None:
            calls = raw.get("tool_calls")
            if not isinstance(calls, list):
                raise ProxyError(
                    "Assistant 'tool_calls' must be an array.", 400, "invalid_request_error"
                )
            message["tool_calls"] = [_normalize_prior_tool_call(call) for call in calls]
        if role == "tool":
            call_id = raw.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ProxyError(
                    "Tool messages require a non-empty 'tool_call_id'.", 400, "invalid_request_error"
                )
            message["tool_call_id"] = call_id
            if isinstance(raw.get("name"), str):
                message["name"] = raw["name"]
        messages.append(message)
    return messages


def _normalize_prior_tool_call(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProxyError("Each assistant tool call must be an object.", 400, "invalid_request_error")
    function = raw.get("function")
    call_id = raw.get("id")
    if not isinstance(function, dict) or not isinstance(call_id, str) or not call_id:
        raise ProxyError("Assistant tool calls require 'id' and 'function'.", 400, "invalid_request_error")
    name = function.get("name")
    arguments = function.get("arguments", "{}")
    if not isinstance(name, str) or not isinstance(arguments, str):
        raise ProxyError(
            "Tool-call function name and arguments must be strings.", 400, "invalid_request_error"
        )
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _anthropic_block_text(content: Any, where: str) -> str:
    """Ép content block của Anthropic về text, từ chối những gì không gửi được."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if not isinstance(content, list):
        raise ProxyError(
            "{} must be a string or an array of content blocks.".format(where),
            400,
            "invalid_request_error",
        )
    pieces: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            value = block.get("text")
            if isinstance(value, str):
                pieces.append(value)
        elif kind == "image":
            raise ProxyError(
                "Image blocks are not supported: the CLI backend takes text only. "
                "Skip the vision step (run.py --skip-vision).",
                400,
                "unsupported_input",
            )
        elif kind in {"tool_use", "tool_result", "document"}:
            raise ProxyError(
                "Content blocks of type {!r} are not supported by this local proxy.".format(kind),
                400,
                "unsupported_input",
            )
    return "\n".join(pieces)


WEB_TOOL_PREFIXES = ("web_search", "web_fetch")


def wants_web_tools(body: Dict[str, Any]) -> bool:
    """True nếu request chỉ khai server tool web_search/web_fetch.

    Anthropic đặt tên có ngày tháng (``web_search_20260209``) nên khớp theo tiền tố.
    CLI có sẵn WebSearch/WebFetch, ánh xạ được. Mọi loại tool khác thì từ chối —
    endpoint này không chạy vòng lặp tool phía client.
    """
    tools = body.get("tools")
    if not tools:
        return False
    if not isinstance(tools, list):
        raise ProxyError("'tools' must be an array.", 400, "invalid_request_error")
    unsupported = []
    for tool in tools:
        kind = tool.get("type", "") if isinstance(tool, dict) else ""
        if not any(kind.startswith(prefix) for prefix in WEB_TOOL_PREFIXES):
            unsupported.append(kind or "?")
    if unsupported:
        raise ProxyError(
            "Only the web_search / web_fetch server tools are supported here; got {}. "
            "The CLI backend runs no client-side tool loop on this endpoint.".format(
                ", ".join(sorted(set(unsupported)))
            ),
            400,
            "unsupported_input",
        )
    return True


def normalize_anthropic_messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Chuẩn hoá request Anthropic Messages về transcript chung."""
    wants_web_tools(body)  # chỉ để loại sớm tool không hỗ trợ
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ProxyError("'messages' must be a non-empty array.", 400, "invalid_request_error")

    messages: List[Dict[str, Any]] = []
    system_text = _anthropic_block_text(body.get("system"), "'system'")
    if system_text:
        messages.append({"role": "system", "content": system_text})
    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            raise ProxyError(
                "Message at index {} must be an object.".format(index), 400, "invalid_request_error"
            )
        role = raw.get("role", "user")
        if role not in {"user", "assistant"}:
            raise ProxyError(
                "Unsupported message role: {!r}. Use 'user' or 'assistant'.".format(role),
                400,
                "unsupported_input",
            )
        messages.append(
            {
                "role": role,
                "content": _anthropic_block_text(
                    raw.get("content"), "Message content at index {}".format(index)
                ),
            }
        )
    return messages


# ── Dựng prompt ──────────────────────────────────────────────────────────────

def _conversation_transcript(messages: List[Dict[str, Any]]) -> str:
    return "\n".join(
        json.dumps(message, ensure_ascii=False, separators=(",", ":")) for message in messages
    )


def build_prompt(messages: List[Dict[str, Any]]) -> str:
    return "<CONVERSATION_JSONL>\n{}\n</CONVERSATION_JSONL>".format(
        _conversation_transcript(messages)
    )


def build_tool_prompt(
    messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], tool_choice: Any
) -> str:
    return (
        "Tool-choice rule: {}\n\n"
        "<APPLICATION_TOOLS_JSON>\n{}\n</APPLICATION_TOOLS_JSON>\n\n"
        "<CONVERSATION_JSONL>\n{}\n</CONVERSATION_JSONL>"
    ).format(
        _tool_choice_instruction(tool_choice),
        json.dumps(tools, ensure_ascii=False),
        _conversation_transcript(messages),
    )


def _tool_choice_instruction(tool_choice: Any) -> str:
    if tool_choice == "required":
        return "You must request at least one available function."
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        name = function.get("name") if isinstance(function, dict) else tool_choice.get("name")
        if isinstance(name, str):
            return "You must request exactly the function named {!r}.".format(name)
    return "Call tools only when needed; otherwise answer directly."


def build_tool_decision_schema(tools: List[Dict[str, Any]], tool_choice: Any) -> Dict[str, Any]:
    """Dựng schema structured-output theo từng request cho ``--json-schema``.

    Ràng buộc tool-choice ngay trong schema (chứ không chỉ trong prompt) để CLI
    tự loại quyết định sai trước khi tới proxy. Cố ý bỏ khoá ``$schema``: CLI từ
    chối tham chiếu draft 2020-12.
    """
    names = [tool["function"]["name"] for tool in tools]
    forced_name = None
    if isinstance(tool_choice, dict):
        forced_name = tool_choice["function"]["name"]

    call_name = {"const": forced_name} if forced_name else {"enum": names}
    calls: Dict[str, Any] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"name": call_name, "arguments": {"type": "string"}},
            "required": ["name", "arguments"],
            "additionalProperties": False,
        },
    }
    must_call = tool_choice == "required" or forced_name is not None
    if must_call:
        calls["minItems"] = 1
    kind = {"const": "tool_calls"} if must_call else {"enum": ["assistant", "tool_calls"]}
    return {
        "type": "object",
        "properties": {"kind": kind, "content": {"type": "string"}, "tool_calls": calls},
        "required": ["kind", "content", "tool_calls"],
        "additionalProperties": False,
    }


def normalize_chat_tools(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_tools = body.get("tools")
    if raw_tools is None or raw_tools == []:
        return []
    if not isinstance(raw_tools, list):
        raise ProxyError("'tools' must be an array.", 400, "invalid_request_error")
    tools: List[Dict[str, Any]] = []
    names = set()
    for raw in raw_tools:
        if not isinstance(raw, dict) or raw.get("type") != "function":
            raise ProxyError("Only OpenAI function tools are supported.", 400, "unsupported_input")
        function = raw.get("function")
        if not isinstance(function, dict):
            raise ProxyError("Function tool metadata is required.", 400, "invalid_request_error")
        name = function.get("name")
        if not isinstance(name, str) or not TOOL_NAME_PATTERN.match(name):
            raise ProxyError(
                "Function names must contain 1-64 letters, digits, underscores, or hyphens.",
                400,
                "invalid_request_error",
            )
        if name in names:
            raise ProxyError("Duplicate function name: {}.".format(name), 400, "invalid_request_error")
        names.add(name)
        parameters = function.get("parameters", {"type": "object", "properties": {}})
        if not isinstance(parameters, dict):
            raise ProxyError(
                "Parameters for function {!r} must be a JSON Schema object.".format(name),
                400,
                "invalid_request_error",
            )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(function.get("description", "")),
                    "parameters": parameters,
                },
            }
        )
    return tools


# ── Tìm CLI & môi trường tiến trình con ──────────────────────────────────────

def _newest_file(paths: List[Path]) -> Optional[str]:
    existing = [path for path in paths if path.is_file()]
    if not existing:
        return None
    return str(max(existing, key=lambda path: path.stat().st_mtime))


def find_claude() -> str:
    """Tìm Claude Code CLI, ưu tiên đường dẫn khai báo tay."""
    configured = os.getenv("CLAUDE_CLI")
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        raise ProxyError("CLAUDE_CLI does not point to a file: {}".format(path))

    on_path = shutil.which("claude") or shutil.which("claude.exe")
    if on_path:
        return on_path

    home = Path.home()
    native = _newest_file([home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude"])
    if native:
        return native

    binary = "claude.exe" if os.name == "nt" else "claude"
    editor_installs: List[Path] = []
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        editor_installs.extend(
            (home / editor / "extensions").glob(
                "anthropic.claude-code-*/resources/native-binary/{}".format(binary)
            )
        )
    editor = _newest_file(editor_installs)
    if editor:
        return editor

    raise ProxyError(
        "Claude Code CLI was not found. Install it (https://claude.com/claude-code), "
        "put 'claude' on PATH, or set CLAUDE_CLI."
    )


def concurrency_limit() -> int:
    """Số tiến trình CLI được chạy cùng lúc. Mỗi tiến trình tốn ~400 MB RSS."""
    try:
        return max(1, int(os.getenv("LLM_PROXY_MAX_CONCURRENCY", "4")))
    except ValueError:
        return 4


# Request vượt hạn mức thì xếp hàng ở đây, không fork thêm tiến trình CLI.
CLI_SLOTS = threading.BoundedSemaphore(concurrency_limit())

# Caller gọi tới qua ANTHROPIC_BASE_URL sẽ export chính biến đó trong môi trường
# mà ta kế thừa. CLI cũng đọc biến này, để nguyên là tiến trình con gọi ngược
# vào proxy — vòng lặp vô hạn. Đặc biệt dễ dính khi crawler và proxy cùng máy.
ENDPOINT_ENV_VARS = ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_API_BASE_URL", "OPENAI_BASE_URL")


def child_environment() -> Dict[str, str]:
    """Kế thừa môi trường, trừ những biến khiến CLI quay ngược lại proxy."""
    return {k: v for k, v in os.environ.items() if k not in ENDPOINT_ENV_VARS}


# ── Backend ──────────────────────────────────────────────────────────────────

class ClaudeBackend:
    """Claude Code CLI, mỗi request một tiến trình dùng một lần."""

    name = "claude"

    def __init__(self, cli: str, runtime_dir: Path, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
        self.cli = cli
        self.runtime_dir = runtime_dir.resolve()
        self.timeout_seconds = timeout_seconds

    def command(
        self,
        model: str,
        system_prompt: str,
        decision: Optional[ToolDecision] = None,
        web_tools: bool = False,
    ) -> List[str]:
        # Mặc định không tool nội trú: model chỉ được trả lời, không chạm vào máy.
        # Chỉ request nào khai server tool web_search/web_fetch mới được mở mạng,
        # và cũng chỉ mở đúng hai tool đó — không có Bash/Read/Edit.
        allowed = "WebSearch,WebFetch" if web_tools else ""
        command = [
            self.cli,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            allowed,
            "--permission-mode",
            "dontAsk",
        ]
        if web_tools:
            # --tools mới chỉ làm cho tool CÓ SẴN; dontAsk vẫn từ chối khi chạy.
            # --allowed-tools mới là thứ duyệt trước, nếu không sẽ bị permission denial.
            command.extend(["--allowed-tools", "WebSearch", "WebFetch"])
        command += [
            "--no-session-persistence",
            "--strict-mcp-config",
            # Bỏ qua CLAUDE.md, skill, plugin, hook, agent riêng; đăng nhập vẫn dùng được.
            "--safe-mode",
            "--system-prompt",
            system_prompt,
            "--model",
            model,
        ]
        if decision is not None:
            schema = build_tool_decision_schema(decision.tools, decision.tool_choice)
            command.extend(
                ["--json-schema", json.dumps(schema, ensure_ascii=False, separators=(",", ":"))]
            )
        return command

    def run(
        self,
        prompt: str,
        model: str,
        system_prompt: str,
        decision: Optional[ToolDecision] = None,
        web_tools: bool = False,
    ) -> RunResult:
        with CLI_SLOTS:
            return self._run_locked(prompt, model, system_prompt, decision, web_tools)

    def _run_locked(
        self, prompt: str, model: str, system_prompt: str,
        decision: Optional[ToolDecision], web_tools: bool = False
    ) -> RunResult:
        process = self._start(prompt, model, system_prompt, decision, web_tools)
        stderr_lines: List[str] = []
        stderr_thread = threading.Thread(
            target=_drain_stream, args=(process.stderr, stderr_lines)
        )
        stderr_thread.daemon = True
        stderr_thread.start()

        state: Dict[str, Any] = {
            "text_parts": [],
            "session_id": None,
            "model": None,
            "result_text": None,
            "structured": None,
            "usage": _empty_usage(),
        }
        timed_out = threading.Event()

        def terminate_on_timeout():
            timed_out.set()
            if process.poll() is None:
                process.kill()

        timeout = threading.Timer(self.timeout_seconds, terminate_on_timeout)
        timeout.daemon = True
        timeout.start()
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    self.consume(event, state)

            return_code = process.wait(timeout=5)
            stderr_thread.join(timeout=1)
            if timed_out.is_set():
                raise ProxyError("The Claude request timed out.", 504, "timeout")
            if return_code != 0:
                detail = "".join(stderr_lines).strip()
                raise ProxyError(
                    self.process_error(detail, return_code), 502, "claude_process_error"
                )
            text = state["result_text"]
            if text is None:
                text = "\n".join(state["text_parts"])
            if not text and state["structured"] is None:
                raise ProxyError(
                    "Claude completed without an assistant message.", 502, "empty_response"
                )
            return RunResult(
                text, state["session_id"], state["model"], state["usage"], state["structured"]
            )
        finally:
            timeout.cancel()
            if process.poll() is None:
                process.kill()

    def _start(
        self, prompt: str, model: str, system_prompt: str,
        decision: Optional[ToolDecision], web_tools: bool = False
    ):
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                self.command(model, system_prompt, decision, web_tools),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1,
                cwd=str(self.runtime_dir),
                env=child_environment(),
                creationflags=creationflags,
            )
        except OSError as exc:
            raise ProxyError("Could not start the Claude CLI: {}".format(exc))

        def write_stdin():
            # Ghi ở luồng riêng để prompt lớn không kẹt với CLI đã bắt đầu xuất
            # stdout trước khi đọc hết stdin.
            try:
                process.stdin.write(prompt)
                process.stdin.close()
            except (BrokenPipeError, ValueError, OSError):
                pass

        writer = threading.Thread(target=write_stdin)
        writer.daemon = True
        writer.start()
        return process

    def consume(self, event: Dict[str, Any], state: Dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "system" and event.get("subtype") == "init":
            state["session_id"] = event.get("session_id")
            if isinstance(event.get("model"), str):
                state["model"] = event["model"]
        elif event_type == "assistant":
            state["text_parts"].extend(_assistant_text(event))
        elif event_type == "result":
            state["usage"] = _claude_usage(event.get("usage"))
            state["structured"] = event.get("structured_output")
            if event.get("is_error") or event.get("subtype") != "success":
                status, code = _result_status(event)
                raise ProxyError(_result_error(event), status, code)
            if isinstance(event.get("result"), str):
                state["result_text"] = event["result"]

    def process_error(self, stderr: str, return_code: int) -> str:
        lowered = stderr.lower()
        if "not logged in" in lowered or ("login" in lowered and "auth" in lowered):
            return "The Claude CLI is not signed in. Run 'claude auth' and retry."
        if "usage limit" in lowered or "rate limit" in lowered:
            return "The Claude usage or rate limit was reached."
        if stderr:
            return "The Claude CLI failed (exit {}): {}".format(
                return_code, stderr.splitlines()[-1][:500]
            )
        return "The Claude CLI failed with exit code {}.".format(return_code)


def _assistant_text(event: Dict[str, Any]) -> List[str]:
    """Chỉ lấy khối text, bỏ khối thinking và tool_use."""
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block["text"]
    ]


def _drain_stream(stream: Any, destination: List[str]) -> None:
    if stream is None:
        return
    for line in stream:
        destination.append(line)


def _empty_usage() -> Dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "prompt_tokens_details": {"cached_tokens": 0},
    }


def _claude_usage(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return _empty_usage()
    cache_read = int(value.get("cache_read_input_tokens") or 0)
    # Prompt caching chia nhỏ input; client OpenAI mong một con số prompt duy nhất.
    prompt = (
        int(value.get("input_tokens") or 0)
        + int(value.get("cache_creation_input_tokens") or 0)
        + cache_read
    )
    completion = int(value.get("output_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cache_read},
    }


def _result_error(event: Dict[str, Any]) -> str:
    subtype = event.get("subtype") or "error"
    status = event.get("api_error_status")
    detail = event.get("result") if isinstance(event.get("result"), str) else ""
    if subtype == "error_max_turns":
        return "Claude stopped after reaching its turn limit."
    if status:
        return "Claude API error ({}): {}".format(status, detail[:300]).strip()
    if detail:
        return "Claude failed ({}): {}".format(subtype, detail[:300])
    return "Claude failed ({}).".format(subtype)


def _result_status(event: Dict[str, Any]) -> Tuple[int, str]:
    """Giữ nguyên status của upstream thay vì nuốt hết thành 502.

    CLI gắn api_error_status khi Claude API từ chối (429 hết hạn mức phiên,
    401 chưa đăng nhập…). Ép về 502 thì SDK anthropic coi là lỗi server: retry
    4 lần vô ích rồi ném InternalServerError, che mất nguyên nhân thật.
    """
    status = event.get("api_error_status")
    if isinstance(status, str) and status.strip().isdigit():
        status = int(status)
    if isinstance(status, int) and 400 <= status < 500:
        return status, "claude_api_error"
    return 502, "claude_error"


# ── Tool decision ────────────────────────────────────────────────────────────

def validate_tool_choice(tool_choice: Any, tools: List[Dict[str, Any]]) -> Any:
    names = {tool["function"]["name"] for tool in tools}
    if tool_choice is None:
        return "auto"
    if isinstance(tool_choice, str) and tool_choice in {"auto", "none", "required"}:
        if tool_choice == "required" and not tools:
            raise ProxyError(
                "tool_choice='required' needs at least one function tool.",
                400,
                "invalid_request_error",
            )
        return tool_choice
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        function = tool_choice.get("function")
        name = function.get("name") if isinstance(function, dict) else tool_choice.get("name")
        if not isinstance(name, str) or name not in names:
            raise ProxyError(
                "Forced tool_choice must name one of the supplied functions.",
                400,
                "invalid_request_error",
            )
        return {"type": "function", "function": {"name": name}}
    raise ProxyError(
        "Unsupported tool_choice. Use 'auto', 'none', 'required', or a named function.",
        400,
        "unsupported_input",
    )


def decision_payload(result: RunResult) -> Any:
    """Ưu tiên structured output do CLI parse sẵn, không có thì parse text."""
    if result.structured is not None:
        return result.structured
    try:
        return json.loads(result.text)
    except json.JSONDecodeError:
        raise ProxyError(
            "Claude returned an invalid structured tool decision.", 502, "invalid_model_output"
        )


def parse_tool_decision(
    value: Any, tools: List[Dict[str, Any]], tool_choice: Any, parallel_tool_calls: bool
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    if not isinstance(value, dict):
        raise ProxyError("The tool decision was not an object.", 502, "invalid_model_output")

    kind = value.get("kind")
    content = value.get("content")
    raw_calls = value.get("tool_calls")
    if not isinstance(content, str) or not isinstance(raw_calls, list):
        raise ProxyError(
            "The tool decision did not match the required schema.", 502, "invalid_model_output"
        )

    required_name = None
    if isinstance(tool_choice, dict):
        required_name = tool_choice["function"]["name"]
    if kind == "assistant":
        if tool_choice == "required" or required_name is not None:
            raise ProxyError(
                "The model answered directly when a tool call was required.",
                502,
                "invalid_model_output",
            )
        return content, []
    if kind != "tool_calls" or not raw_calls:
        raise ProxyError(
            "The model did not return a valid assistant answer or tool call.",
            502,
            "invalid_model_output",
        )

    available = {tool["function"]["name"] for tool in tools}
    calls: List[Dict[str, Any]] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            raise ProxyError("The model returned an invalid tool call.", 502, "invalid_model_output")
        name = raw.get("name")
        encoded = raw.get("arguments")
        try:
            arguments = json.loads(encoded) if isinstance(encoded, str) else None
        except json.JSONDecodeError:
            arguments = None
        if name not in available or not isinstance(arguments, dict):
            raise ProxyError(
                "The model requested an unavailable tool or invalid arguments.",
                502,
                "invalid_model_output",
            )
        if required_name is not None and name != required_name:
            raise ProxyError(
                "The model requested a different function than tool_choice required.",
                502,
                "invalid_model_output",
            )
        calls.append(
            {
                "id": "call_{}".format(uuid.uuid4().hex[:24]),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                },
            }
        )
    if not parallel_tool_calls and len(calls) > 1:
        calls = calls[:1]
    return None, calls


# ── HTTP ─────────────────────────────────────────────────────────────────────

class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, backend: ClaudeBackend, api_key: Optional[str]):
        ThreadingHTTPServer.__init__(self, address, ProxyHandler)
        self.backend = backend
        self.api_key = api_key


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if self.path in {"/", "/v1", "/v1/"}:
            host = self.headers.get("Host", "127.0.0.1:11439")
            self._json(
                200,
                {
                    "name": "Claude Local Proxy",
                    "status": "ok",
                    "base_url": "http://{}".format(host),
                    "model": default_model(),
                    "model_alias": MODEL_ALIAS,
                    "endpoints": {
                        "messages": "POST /v1/messages",
                        "chat_completions": "POST /v1/chat/completions",
                        "responses": "POST /v1/responses",
                        "models": "GET /v1/models",
                        "health": "GET /healthz",
                    },
                    "note": "Mở base URL không gọi model; hãy POST tới một endpoint completion.",
                },
            )
            return
        if self.path in {"/healthz", "/v1/health"}:
            self._json(200, {"status": "ok", "model": default_model()})
            return
        if self.path == "/v1/models":
            if not self._authorized():
                return
            created = int(time.time())
            listed = [default_model(), MODEL_ALIAS] + sorted(CLI_MODEL_ALIASES)
            seen = []
            data = []
            for name in listed:
                if name in seen:
                    continue
                seen.append(name)
                data.append(
                    {
                        "id": name,
                        "object": "model",
                        "created": created,
                        "owned_by": "local-claude-cli",
                    }
                )
            self._json(200, {"object": "list", "data": data})
            return
        self._error(404, "Route not found.", "not_found")

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            return
        try:
            body = self._read_json()
            if self.path == "/v1/messages":
                self._messages(body)
            elif self.path == "/v1/chat/completions":
                self._chat_completions(body)
            elif self.path == "/v1/responses":
                self._responses(body)
            else:
                self._error(404, "Route not found.", "not_found")
        except ProxyError as exc:
            self._error(exc.status, str(exc), exc.code)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self._error(500, "The local proxy failed unexpectedly.", "internal_error")

    def _messages(self, body):
        """Anthropic Messages API — đường mà pipeline/llm.py dùng."""
        model = resolve_model(body)
        web_tools = wants_web_tools(body)
        messages = normalize_anthropic_messages(body)
        result = self.server.backend.run(
            build_prompt(messages),
            model,
            WEB_SYSTEM_PROMPT if web_tools else TEXT_SYSTEM_PROMPT,
            web_tools=web_tools,
        )
        message_id = "msg_local_{}".format(uuid.uuid4().hex[:24])
        usage = {
            "input_tokens": result.usage["prompt_tokens"],
            "output_tokens": result.usage["completion_tokens"],
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": result.usage["prompt_tokens_details"]["cached_tokens"],
        }
        response = {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": result.model or model,
            "content": [{"type": "text", "text": result.text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": usage,
        }
        if body.get("stream") is not True:
            self._json(200, response)
            return
        # SDK dựng lại message từ đúng chuỗi sự kiện này, nên phải phát đủ.
        start = dict(response)
        start["content"] = []
        start["stop_reason"] = None
        self._sse(
            [
                {"type": "message_start", "message": start},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": result.text},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": usage["output_tokens"]},
                },
                {"type": "message_stop"},
            ],
            done=False,
            named=True,
        )

    def _chat_completions(self, body):
        _reject_unsupported(body)
        model = resolve_model(body)
        messages = normalize_messages(body, "chat")
        tools = normalize_chat_tools(body)
        tool_choice = validate_tool_choice(body.get("tool_choice"), tools)
        if tools and tool_choice != "none":
            result = self.server.backend.run(
                build_tool_prompt(messages, tools, tool_choice),
                model,
                TOOL_SYSTEM_PROMPT,
                ToolDecision(tools, tool_choice),
            )
            content, tool_calls = parse_tool_decision(
                decision_payload(result), tools, tool_choice,
                body.get("parallel_tool_calls") is not False,
            )
        else:
            result = self.server.backend.run(build_prompt(messages), model, TEXT_SYSTEM_PROMPT)
            content, tool_calls = result.text, []
        reported = result.model or model
        response_id = "chatcmpl-local-{}".format(uuid.uuid4().hex)
        created = int(time.time())
        finish_reason = "tool_calls" if tool_calls else "stop"
        if body.get("stream") is True:
            delta: Dict[str, Any] = {"role": "assistant"}
            if tool_calls:
                delta["tool_calls"] = [
                    dict({"index": i}, **call) for i, call in enumerate(tool_calls)
                ]
            else:
                delta["content"] = content
            self._sse(
                [
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": reported,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    },
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": reported,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                        "usage": result.usage,
                    },
                ],
                done=True,
            )
            return
        message: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        self._json(
            200,
            {
                "id": response_id,
                "object": "chat.completion",
                "created": created,
                "model": reported,
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": result.usage,
                "system_fingerprint": None,
            },
        )

    def _responses(self, body):
        _reject_unsupported(body)
        if body.get("tools"):
            raise ProxyError(
                "Responses API tools are not supported; use Chat Completions.",
                400,
                "unsupported_input",
            )
        model = resolve_model(body)
        messages = normalize_messages(body, "responses")
        result = self.server.backend.run(build_prompt(messages), model, TEXT_SYSTEM_PROMPT)
        response_id = "resp_local_{}".format(uuid.uuid4().hex)
        message_id = "msg_local_{}".format(uuid.uuid4().hex)
        response = {
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": result.model or model,
            "output": [
                {
                    "id": message_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": result.text, "annotations": []}
                    ],
                }
            ],
            "usage": {
                "input_tokens": result.usage["prompt_tokens"],
                "output_tokens": result.usage["completion_tokens"],
                "total_tokens": result.usage["total_tokens"],
            },
            "error": None,
        }
        if body.get("stream") is True:
            in_progress = dict(response)
            in_progress["status"] = "in_progress"
            in_progress["output"] = []
            self._sse(
                [
                    {"type": "response.created", "response": in_progress},
                    {"type": "response.output_text.delta", "response_id": response_id,
                     "item_id": message_id, "output_index": 0, "content_index": 0,
                     "delta": result.text},
                    {"type": "response.output_text.done", "response_id": response_id,
                     "item_id": message_id, "output_index": 0, "content_index": 0,
                     "text": result.text},
                    {"type": "response.completed", "response": response},
                ],
                done=False,
                named=True,
            )
            return
        self._json(200, response)

    def _read_json(self) -> Dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError:
            raise ProxyError("Invalid Content-Length.", 400, "invalid_request_error")
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ProxyError(
                "Request body must be between 1 and {} bytes.".format(MAX_BODY_BYTES),
                413 if length > MAX_BODY_BYTES else 400,
                "invalid_request_error",
            )
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ProxyError("Request body must be valid JSON.", 400, "invalid_request_error")
        if not isinstance(value, dict):
            raise ProxyError("Request body must be a JSON object.", 400, "invalid_request_error")
        return value

    def _authorized(self) -> bool:
        expected = self.server.api_key
        if not expected:
            return True
        # Client OpenAI gửi bearer token; SDK anthropic gửi x-api-key.
        if self.headers.get("Authorization") == "Bearer {}".format(expected):
            return True
        if self.headers.get("x-api-key") == expected:
            return True
        self._error(401, "Missing or invalid API key.", "invalid_api_key")
        return False

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def _error(self, status: int, message: str, code: str) -> None:
        # Chỉ có dòng access log thì chạy nền không biết vì sao hỏng.
        if status >= 500:
            self.log_message("%s %s -> %s: %s", self.command, self.path, code, message)
        if self.path == "/v1/messages":
            # SDK anthropic mong đúng phong bì lỗi của nó.
            anthropic_type = {
                400: "invalid_request_error",
                401: "authentication_error",
                403: "permission_error",
                404: "not_found_error",
                413: "request_too_large",
                429: "rate_limit_error",
                504: "timeout_error",
            }.get(status, "api_error")
            self._json(status, {"type": "error", "error": {"type": anthropic_type, "message": message}})
            return
        self._json(
            status,
            {
                "error": {
                    "message": message,
                    "type": "invalid_request_error" if status < 500 else "server_error",
                    "param": None,
                    "code": code,
                }
            },
        )

    def _sse(self, events: List[Dict[str, Any]], done: bool, named: bool = False) -> None:
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in events:
            prefix = "event: {}\n".format(event["type"]) if named else ""
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write("{}data: {}\n\n".format(prefix, data).encode("utf-8"))
            self.wfile.flush()
        if done:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, x-api-key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[{}] {}\n".format(self.log_date_time_string(), fmt % args))


def _reject_unsupported(body: Dict[str, Any]) -> None:
    if body.get("n", 1) != 1:
        raise ProxyError("Only n=1 is supported.", 400, "unsupported_input")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claude CLI proxy cho pipeline WS1")
    parser.add_argument("--host", default=os.getenv("LLM_PROXY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LLM_PROXY_PORT", "11439")))
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("LLM_PROXY_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))),
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    api_key = (os.getenv("LLM_PROXY_API_KEY") or "").strip() or None
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not api_key:
        raise SystemExit("Từ chối bind ra ngoài loopback khi chưa đặt LLM_PROXY_API_KEY.")
    runtime_dir = Path(__file__).resolve().parent / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    try:
        backend = ClaudeBackend(find_claude(), runtime_dir, args.timeout)
    except ProxyError as exc:
        raise SystemExit(str(exc))
    server = ProxyServer((args.host, args.port), backend, api_key)
    print("Claude proxy: http://{}:{}".format(args.host, args.port))
    print("  CLI          : {}".format(backend.cli))
    print("  model mặc định: {}".format(default_model()))
    print("  đồng thời    : {} tiến trình · timeout {}s".format(concurrency_limit(), args.timeout))
    print("  xác thực     : {}".format("bật (LLM_PROXY_API_KEY)" if api_key else "tắt"))
    print("Ctrl+C để dừng.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang dừng.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
