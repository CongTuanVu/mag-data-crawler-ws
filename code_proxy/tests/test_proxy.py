"""Test cho code_proxy/proxy.py. Chạy: python3 -m unittest discover -s code_proxy/tests"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy import (  # noqa: E402
    ClaudeBackend,
    ProxyError,
    RunResult,
    ToolDecision,
    _assistant_text,
    _claude_usage,
    build_prompt,
    build_tool_decision_schema,
    build_tool_prompt,
    child_environment,
    decision_payload,
    default_model,
    find_claude,
    normalize_anthropic_messages,
    normalize_chat_tools,
    normalize_messages,
    parse_tool_decision,
    resolve_model,
    validate_tool_choice,
    wants_web_tools,
)


def backend() -> ClaudeBackend:
    return ClaudeBackend("/usr/local/bin/claude", Path("."), 300)


def lookup_tools():
    return normalize_chat_tools(
        {"tools": [{"type": "function",
                    "function": {"name": "lookup", "parameters": {"type": "object"}}}]}
    )


class AnthropicMessagesTests(unittest.TestCase):
    """Đường mà pipeline/llm.py thực sự dùng."""

    def test_system_becomes_the_first_message(self):
        messages = normalize_anthropic_messages(
            {"system": [{"type": "text", "text": "Chỉ trả JSON."}],
             "messages": [{"role": "user", "content": "Hi"}]}
        )
        self.assertEqual(messages, [{"role": "system", "content": "Chỉ trả JSON."},
                                    {"role": "user", "content": "Hi"}])

    def test_plain_string_system_and_content(self):
        messages = normalize_anthropic_messages(
            {"system": "Ngắn gọn.", "messages": [{"role": "user", "content": "Hi"}]}
        )
        self.assertEqual(messages[0]["content"], "Ngắn gọn.")
        self.assertEqual(messages[1]["content"], "Hi")

    def test_absent_system_is_omitted(self):
        self.assertEqual(
            normalize_anthropic_messages({"messages": [{"role": "user", "content": "Hi"}]}),
            [{"role": "user", "content": "Hi"}],
        )

    def test_assistant_turns_are_kept(self):
        messages = normalize_anthropic_messages(
            {"messages": [{"role": "user", "content": "One"},
                          {"role": "assistant", "content": [{"type": "text", "text": "Two"}]},
                          {"role": "user", "content": "Three"}]}
        )
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "Two")

    def test_image_blocks_point_at_the_vision_workaround(self):
        with self.assertRaisesRegex(ProxyError, "skip-vision"):
            normalize_anthropic_messages(
                {"messages": [{"role": "user",
                               "content": [{"type": "image", "source": {"data": "x"}}]}]}
            )

    def test_web_server_tools_are_accepted(self):
        # Bước discover khai đúng hai tool này; CLI có WebSearch/WebFetch tương đương.
        body = {"tools": [{"type": "web_search_20260209", "name": "web_search"},
                          {"type": "web_fetch_20260209", "name": "web_fetch"}],
                "messages": [{"role": "user", "content": "Hi"}]}
        self.assertTrue(wants_web_tools(body))
        self.assertEqual(normalize_anthropic_messages(body),
                         [{"role": "user", "content": "Hi"}])

    def test_no_tools_means_no_web_access(self):
        self.assertFalse(wants_web_tools({"messages": [{"role": "user", "content": "Hi"}]}))

    def test_other_tool_types_are_still_refused(self):
        with self.assertRaisesRegex(ProxyError, "web_search / web_fetch"):
            wants_web_tools(
                {"tools": [{"type": "function", "name": "get_weather"}],
                 "messages": [{"role": "user", "content": "Hi"}]}
            )

    def test_web_tools_flip_the_cli_tool_allowlist(self):
        # Mặc định khoá hết; chỉ request khai web tool mới mở, và chỉ mở 2 tool đó.
        self.assertEqual(backend().command("sonnet", "S")[
            backend().command("sonnet", "S").index("--tools") + 1], "")
        with_web = backend().command("sonnet", "S", None, web_tools=True)
        self.assertEqual(with_web[with_web.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertNotIn("Bash", with_web[with_web.index("--tools") + 1])
        # --tools chỉ làm tool có sẵn; --allowed-tools mới duyệt trước để dontAsk
        # không từ chối lúc chạy.
        i = with_web.index("--allowed-tools")
        self.assertEqual(with_web[i + 1:i + 3], ["WebSearch", "WebFetch"])
        self.assertNotIn("--allowed-tools", backend().command("sonnet", "S"))

    def test_empty_message_list_is_rejected(self):
        with self.assertRaisesRegex(ProxyError, "non-empty array"):
            normalize_anthropic_messages({"messages": []})

    def test_unknown_role_is_rejected(self):
        with self.assertRaisesRegex(ProxyError, "Unsupported message role"):
            normalize_anthropic_messages({"messages": [{"role": "tool", "content": "x"}]})


class OpenAIRequestTests(unittest.TestCase):
    def test_chat_messages_become_a_jsonl_prompt(self):
        messages = normalize_messages(
            {"messages": [{"role": "system", "content": "Ngắn gọn."},
                          {"role": "user", "content": "Hello"}]}, "chat"
        )
        prompt = build_prompt(messages)
        self.assertIn('"role":"user","content":"Hello"', prompt)
        self.assertIn("<CONVERSATION_JSONL>", prompt)

    def test_responses_string_input(self):
        self.assertEqual(
            normalize_messages({"instructions": "Ngắn.", "input": "Hello"}, "responses"),
            [{"role": "developer", "content": "Ngắn."}, {"role": "user", "content": "Hello"}],
        )

    def test_text_parts_are_joined(self):
        messages = normalize_messages(
            {"messages": [{"role": "user", "content": [{"type": "text", "text": "one"},
                                                       {"type": "text", "text": "two"}]}]}, "chat"
        )
        self.assertEqual(messages[0]["content"], "one\ntwo")

    def test_image_parts_fail_clearly(self):
        with self.assertRaisesRegex(ProxyError, "Image inputs"):
            normalize_messages(
                {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]},
                "chat",
            )

    def test_tool_messages_are_preserved(self):
        messages = normalize_messages(
            {"messages": [
                {"role": "user", "content": "Weather?"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "call_123", "type": "function",
                                 "function": {"name": "get_weather",
                                              "arguments": '{"city":"Hanoi"}'}}]},
                {"role": "tool", "tool_call_id": "call_123", "name": "get_weather",
                 "content": "31 C"}]}, "chat"
        )
        self.assertEqual(messages[1]["tool_calls"][0]["id"], "call_123")
        self.assertEqual(messages[2]["tool_call_id"], "call_123")


class ModelTests(unittest.TestCase):
    def test_default_model(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(default_model(), "claude-sonnet-5")

    def test_aliases_and_full_names(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(resolve_model({}), "claude-sonnet-5")
            self.assertEqual(resolve_model({"model": "claude-local"}), "claude-sonnet-5")
            self.assertEqual(resolve_model({"model": "opus"}), "opus")
            self.assertEqual(resolve_model({"model": "claude-opus-5"}), "claude-opus-5")

    def test_non_claude_model_is_rejected(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ProxyError, "Unknown model"):
                resolve_model({"model": "gpt-4o"})

    def test_env_override(self):
        with patch.dict("os.environ", {"CLAUDE_PROXY_MODEL": "claude-opus-5"}, clear=True):
            self.assertEqual(default_model(), "claude-opus-5")
            self.assertEqual(resolve_model({"model": "claude-local"}), "claude-opus-5")


class CliDiscoveryTests(unittest.TestCase):
    def test_path_lookup_wins(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("shutil.which", return_value="/usr/local/bin/claude"):
                self.assertEqual(find_claude(), "/usr/local/bin/claude")

    def test_missing_cli_raises_actionable_error(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("shutil.which", return_value=None):
                with patch("pathlib.Path.glob", return_value=iter([])):
                    with patch("pathlib.Path.is_file", return_value=False):
                        with self.assertRaisesRegex(ProxyError, "was not found"):
                            find_claude()


class ChildEnvironmentTests(unittest.TestCase):
    def test_endpoint_vars_are_dropped_so_the_cli_cannot_loop_back(self):
        with patch.dict("os.environ",
                        {"ANTHROPIC_BASE_URL": "http://127.0.0.1:11439",
                         "CLAUDE_CODE_API_BASE_URL": "http://127.0.0.1:11439",
                         "OPENAI_BASE_URL": "http://127.0.0.1:11439/v1",
                         "PATH": "/usr/bin"}, clear=True):
            env = child_environment()
            self.assertNotIn("ANTHROPIC_BASE_URL", env)
            self.assertNotIn("CLAUDE_CODE_API_BASE_URL", env)
            self.assertNotIn("OPENAI_BASE_URL", env)
            self.assertEqual(env["PATH"], "/usr/bin")

    def test_credentials_are_left_alone(self):
        with patch.dict("os.environ",
                        {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "HOME": "/home/svc"}, clear=True):
            env = child_environment()
            self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "tok")
            self.assertEqual(env["HOME"], "/home/svc")


class CommandTests(unittest.TestCase):
    def test_command_disables_tools_and_persistence(self):
        command = backend().command("claude-opus-5", "SYSTEM")
        self.assertEqual(command[1], "--print")
        self.assertIn("stream-json", command)
        # --tools rỗng là thứ giữ model không chạm vào máy.
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", command)
        self.assertIn("--safe-mode", command)
        self.assertEqual(command[command.index("--system-prompt") + 1], "SYSTEM")
        self.assertEqual(command[command.index("--model") + 1], "claude-opus-5")
        self.assertNotIn("--json-schema", command)

    def test_json_schema_is_inline_without_a_draft_ref(self):
        tools = lookup_tools()
        command = backend().command("claude-opus-5", "SYSTEM", ToolDecision(tools, "auto"))
        encoded = command[command.index("--json-schema") + 1]
        # CLI từ chối schema mang tham chiếu $schema draft 2020-12.
        self.assertNotIn("$schema", encoded)
        self.assertEqual(json.loads(encoded), build_tool_decision_schema(tools, "auto"))


class EventParsingTests(unittest.TestCase):
    def test_only_text_blocks_are_collected(self):
        event = {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "t1", "name": "StructuredOutput", "input": {}}]}}
        self.assertEqual(_assistant_text(event), ["hello"])

    def test_cached_prompt_tokens_fold_into_the_prompt_total(self):
        self.assertEqual(
            _claude_usage({"input_tokens": 1, "cache_creation_input_tokens": 1797,
                           "cache_read_input_tokens": 3289, "output_tokens": 6}),
            {"prompt_tokens": 5087, "completion_tokens": 6, "total_tokens": 5093,
             "prompt_tokens_details": {"cached_tokens": 3289}},
        )

    def test_failed_result_raises(self):
        state = {"usage": {}, "structured": None, "result_text": None}
        with self.assertRaisesRegex(ProxyError, "overloaded"):
            backend().consume(
                {"type": "result", "subtype": "error_during_execution", "is_error": True,
                 "api_error_status": "overloaded", "result": ""}, state)

    def test_structured_output_is_preferred(self):
        result = RunResult('{"kind":"assistant","content":"cũ","tool_calls":[]}', "s",
                           "claude-opus-5", {},
                           {"kind": "assistant", "content": "mới", "tool_calls": []})
        self.assertEqual(decision_payload(result)["content"], "mới")

    def test_unparseable_output_becomes_502(self):
        with self.assertRaisesRegex(ProxyError, "invalid structured tool decision"):
            decision_payload(RunResult("not json", None, None, {}))


class ToolDecisionTests(unittest.TestCase):
    def weather_tools(self):
        return normalize_chat_tools({"tools": [{"type": "function", "function": {
            "name": "get_weather", "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                           "required": ["city"]}}}]})

    def test_decision_uses_openai_shape(self):
        tools = self.weather_tools()
        content, calls = parse_tool_decision(
            {"kind": "tool_calls", "content": "",
             "tool_calls": [{"name": "get_weather", "arguments": '{"city":"Hanoi"}'}]},
            tools, validate_tool_choice("auto", tools), True)
        self.assertIsNone(content)
        self.assertEqual(calls[0]["function"]["name"], "get_weather")
        self.assertEqual(calls[0]["function"]["arguments"], '{"city":"Hanoi"}')

    def test_tool_prompt_carries_definitions(self):
        tools = self.weather_tools()
        prompt = build_tool_prompt([{"role": "user", "content": "Weather?"}], tools, "auto")
        self.assertIn("APPLICATION_TOOLS_JSON", prompt)
        self.assertIn("get_weather", prompt)

    def test_auto_schema_allows_answer_or_call(self):
        schema = build_tool_decision_schema(self.weather_tools(), "auto")
        self.assertEqual(schema["properties"]["kind"], {"enum": ["assistant", "tool_calls"]})
        self.assertNotIn("minItems", schema["properties"]["tool_calls"])

    def test_required_schema_forbids_direct_answer(self):
        schema = build_tool_decision_schema(self.weather_tools(), "required")
        self.assertEqual(schema["properties"]["kind"], {"const": "tool_calls"})
        self.assertEqual(schema["properties"]["tool_calls"]["minItems"], 1)

    def test_forced_schema_pins_the_function_name(self):
        tools = self.weather_tools()
        choice = validate_tool_choice({"type": "function", "function": {"name": "get_weather"}}, tools)
        schema = build_tool_decision_schema(tools, choice)
        self.assertEqual(schema["properties"]["tool_calls"]["items"]["properties"]["name"],
                         {"const": "get_weather"})

    def test_answering_when_a_call_was_required_is_rejected(self):
        with self.assertRaisesRegex(ProxyError, "when a tool call was required"):
            parse_tool_decision({"kind": "assistant", "content": "Ấm.", "tool_calls": []},
                                self.weather_tools(), "required", True)

    def test_parallel_tool_calls_false_truncates(self):
        tools = normalize_chat_tools({"tools": [
            {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}}]})
        decision = {"kind": "tool_calls", "content": "",
                    "tool_calls": [{"name": "a", "arguments": "{}"},
                                   {"name": "b", "arguments": "{}"}]}
        _, both = parse_tool_decision(decision, tools, "required", True)
        _, one = parse_tool_decision(decision, tools, "required", False)
        self.assertEqual(len(both), 2)
        self.assertEqual(len(one), 1)


if __name__ == "__main__":
    unittest.main()
