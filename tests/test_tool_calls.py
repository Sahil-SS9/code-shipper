"""Tests for tool-call support through the broker and the adapter.

Without this an agent inside the container can read a prompt and reply, but cannot touch a file:
Hermes sends tool definitions, the provider answers with a tool call, and if either layer drops that
the model never gets to act. These tests cover the whole path, including the refusals that keep the
channel narrow.
"""

from __future__ import annotations

import json
import unittest

from code_shipper.inference.broker import Broker
from code_shipper.inference.upstream import StaticUpstream

FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}

TOOL_CALL = [
    {
        "id": "call_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "calc/__init__.py"}'},
    }
]


def request(capability: str, **over: object) -> bytes:
    payload = {
        "op": "complete",
        "capability": capability,
        "provider": "local",
        "model": "m",
        "messages": [{"role": "user", "content": "read the file"}],
        "max_tokens": 64,
    }
    payload.update(over)
    return json.dumps(payload).encode()


class ToolPassThroughTests(unittest.TestCase):
    def setUp(self) -> None:
        self.upstream = StaticUpstream("ok")
        self.broker = Broker(self.upstream)
        self.cap = self.broker.issue_capability(run_id="r", provider="local", model="m")

    def test_tool_definitions_reach_the_upstream(self) -> None:
        response = self.broker.handle(request(self.cap.token, tools=[FILE_TOOL]))
        self.assertTrue(response["ok"], response)
        sent = self.upstream.calls[0]["tools"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["function"]["name"], "read_file")

    def test_tool_choice_is_passed_through(self) -> None:
        self.broker.handle(request(self.cap.token, tools=[FILE_TOOL], tool_choice="auto"))
        self.assertEqual(self.upstream.calls[0]["tool_choice"], "auto")

    def test_a_tool_call_with_no_text_is_a_valid_completion(self) -> None:
        upstream = StaticUpstream("", tool_calls=TOOL_CALL)
        broker = Broker(upstream)
        cap = broker.issue_capability(run_id="r", provider="local", model="m")

        response = broker.handle(request(cap.token, tools=[FILE_TOOL]))

        self.assertTrue(response["ok"], response)
        self.assertEqual(response["text"], "")
        self.assertEqual(response["tool_calls"], TOOL_CALL)
        self.assertEqual(response["finish_reason"], "tool_calls")

    def test_neither_text_nor_tool_calls_is_still_a_refusal(self) -> None:
        upstream = StaticUpstream("")
        broker = Broker(upstream)
        cap = broker.issue_capability(run_id="r", provider="local", model="m")
        response = broker.handle(request(cap.token))
        self.assertEqual(response["error"], "upstream_error")


class ToolRefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.broker = Broker(StaticUpstream("ok"))
        self.cap = self.broker.issue_capability(run_id="r", provider="local", model="m")

    def assertRefused(self, code: str, **over: object) -> None:
        response = self.broker.handle(request(self.cap.token, **over))
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["error"], code)

    def test_refuses_a_non_function_tool_type(self) -> None:
        # no code interpreter, no retrieval, no provider-hosted tool
        tool = {"type": "code_interpreter", "function": {"name": "run", "parameters": {}}}
        self.assertRefused("unsupported_tool_type", tools=[tool])

    def test_refuses_a_tool_without_a_name(self) -> None:
        self.assertRefused("bad_request", tools=[{"type": "function", "function": {"parameters": {}}}])

    def test_refuses_extra_fields_on_a_tool(self) -> None:
        tool = dict(FILE_TOOL)
        tool["handler"] = "os.system"
        self.assertRefused("bad_request", tools=[tool])

    def test_refuses_extra_fields_inside_the_function(self) -> None:
        tool = {
            "type": "function",
            "function": {"name": "x", "parameters": {}, "execute": "rm -rf /"},
        }
        self.assertRefused("bad_request", tools=[tool])

    def test_refuses_too_many_tools(self) -> None:
        self.assertRefused("too_many_tools", tools=[FILE_TOOL] * 33)

    def test_refuses_an_oversized_tool_schema(self) -> None:
        huge = {
            "type": "function",
            "function": {
                "name": "big",
                "parameters": {"description": "x" * 20_000},
            },
        }
        self.assertRefused("tool_schema_too_large", tools=[huge])

    def test_refuses_tools_that_are_not_a_list(self) -> None:
        self.assertRefused("bad_request", tools={"name": "x"})

    def test_refuses_a_bad_tool_choice_type(self) -> None:
        self.assertRefused("bad_request", tools=[FILE_TOOL], tool_choice=42)

    def test_accepts_the_three_modes(self) -> None:
        for mode in ("auto", "none", "required"):
            with self.subTest(mode=mode):
                broker = Broker(StaticUpstream("ok"))
                cap = broker.issue_capability(run_id="r", provider="local", model="m")
                response = broker.handle(
                    request(cap.token, tools=[FILE_TOOL], tool_choice=mode)
                )
                self.assertTrue(response["ok"], response)

    def test_accepts_a_named_declared_function(self) -> None:
        broker = Broker(StaticUpstream("ok"))
        cap = broker.issue_capability(run_id="r", provider="local", model="m")
        response = broker.handle(
            request(
                cap.token,
                tools=[FILE_TOOL],
                tool_choice={"type": "function", "function": {"name": "read_file"}},
            )
        )
        self.assertTrue(response["ok"], response)

    def test_refuses_an_undeclared_function_name(self) -> None:
        # asking for a tool that was never declared is a capability request, not a preference
        self.assertRefused(
            "tool_not_declared",
            tools=[FILE_TOOL],
            tool_choice={"type": "function", "function": {"name": "write_file"}},
        )

    def test_refuses_an_arbitrary_dict(self) -> None:
        for bad in (
            {"type": "function"},
            {"type": "function", "function": {"name": "read_file", "strict": True}},
            {"type": "code_interpreter"},
            {"function": {"name": "read_file"}},
            {"name": "read_file"},
        ):
            with self.subTest(choice=bad):
                self.assertRefused("bad_request", tools=[FILE_TOOL], tool_choice=bad)

    def test_refuses_an_unknown_mode_string(self) -> None:
        for bad in ("always", "REQUIRED", "", "auto "):
            with self.subTest(mode=bad):
                self.assertRefused("bad_request", tools=[FILE_TOOL], tool_choice=bad)


class ToolRoundTripMessageTests(unittest.TestCase):
    """The message shapes an agent needs to actually act on a tool result."""

    def setUp(self) -> None:
        self.upstream = StaticUpstream("ok")
        self.broker = Broker(self.upstream)
        self.cap = self.broker.issue_capability(run_id="r", provider="local", model="m")

    def send(self, messages: list) -> dict:
        return self.broker.handle(request(self.cap.token, messages=messages))

    def test_assistant_tool_call_with_null_content_is_accepted(self) -> None:
        response = self.send(
            [
                {"role": "user", "content": "read it"},
                {"role": "assistant", "content": None, "tool_calls": TOOL_CALL},
            ]
        )
        self.assertTrue(response["ok"], response)
        sent = self.upstream.calls[0]["messages"][1]
        self.assertIsNone(sent["content"])
        self.assertEqual(sent["tool_calls"][0]["function"]["name"], "read_file")

    def test_tool_result_with_tool_call_id_is_accepted(self) -> None:
        response = self.send(
            [
                {"role": "user", "content": "read it"},
                {"role": "assistant", "content": None, "tool_calls": TOOL_CALL},
                {"role": "tool", "content": "def divide(a,b): return a*b", "tool_call_id": "call_1"},
            ]
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(self.upstream.calls[0]["messages"][2]["tool_call_id"], "call_1")

    def test_tool_calls_are_bounded_and_stripped(self) -> None:
        call = dict(TOOL_CALL[0])
        call["evil"] = "rm -rf /"
        response = self.send([{"role": "assistant", "content": None, "tool_calls": [call]}])
        self.assertEqual(response["error"], "bad_request")

    def test_arguments_must_be_a_string(self) -> None:
        call = {
            "id": "c",
            "type": "function",
            "function": {"name": "read_file", "arguments": {"path": "x"}},
        }
        response = self.send([{"role": "assistant", "content": None, "tool_calls": [call]}])
        self.assertEqual(response["error"], "bad_request")

    def test_content_parts_are_refused(self) -> None:
        response = self.send(
            [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://x"}}]}]
        )
        self.assertEqual(response["error"], "unsupported_content")

    def test_tool_call_id_on_a_non_tool_message_is_refused(self) -> None:
        response = self.send([{"role": "user", "content": "hi", "tool_call_id": "c1"}])
        self.assertEqual(response["error"], "bad_request")


class UpstreamToolParsingTests(unittest.TestCase):
    """The upstream client must parse a tool-call response instead of treating it as empty."""

    def test_tool_call_only_response_is_accepted(self) -> None:
        from code_shipper.inference.upstream import Completion

        completion = Completion(text="", tool_calls=TOOL_CALL, finish_reason="tool_calls")
        self.assertEqual(completion.tool_calls, TOOL_CALL)
        self.assertEqual(completion.text, "")


if __name__ == "__main__":
    unittest.main()
