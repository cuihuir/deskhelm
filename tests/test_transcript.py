from __future__ import annotations

import unittest

from deskhelm_client.transcript import MAX_OUTPUT_CHARS, Transcript


def _event(kind: str, payload: dict, *, correlation: str = "", event_id: str = "", at: int = 1000) -> dict:
    return {"event_id": event_id or f"e-{kind}-{correlation}-{at}", "kind": kind, "correlation_id": correlation,
            "occurred_at": at, "payload": payload}


def _message(ref: str, role: str, phase: str, text: str) -> dict:
    return _event("message", {"role": role, "phase": phase, "text": text}, correlation=ref)


def _tool(ref: str, phase: str, *, name: str = "command", text: str = "", **extra) -> dict:
    return _event("tool", {"tool_call_id": ref, "name": name, "phase": phase, "text": text, **extra}, correlation=ref)


def _replay(patches: list[dict], items: dict[str, dict]) -> None:
    """Apply patches the way the page does."""
    for patch in patches:
        if patch["op"] == "upsert":
            items[patch["item"]["id"]] = dict(patch["item"])
        elif patch["op"] == "append":
            item = items[patch["id"]]
            item[patch["field"]] = item[patch["field"]][patch.get("trim", 0):] + patch["text"]
        elif patch["op"] == "remove":
            items.pop(patch["id"], None)


class TranscriptTests(unittest.TestCase):
    def test_a_streamed_reply_is_one_message_built_from_appends(self) -> None:
        transcript = Transcript()
        first = transcript.apply(_message("a1", "assistant", "delta", "Hel"))
        second = transcript.apply(_message("a1", "assistant", "delta", "lo"))
        done = transcript.apply(_message("a1", "assistant", "complete", "Hello."))
        late = transcript.apply(_message("a1", "assistant", "delta", " again"))

        self.assertEqual(first, [{"op": "upsert", "item": {
            "id": "m:a1", "type": "message", "seq": 1, "at": 1000, "role": "assistant", "text": "Hel", "streaming": True}}])
        self.assertEqual(second, [{"op": "append", "id": "m:a1", "field": "text", "text": "lo"}])
        self.assertEqual(done[0]["item"]["text"], "Hello.")
        self.assertFalse(done[0]["item"]["streaming"])
        self.assertEqual(late, [])
        self.assertEqual([item["text"] for item in transcript.items()], ["Hello."])

    def test_a_prompt_from_deskhelm_is_keyed_by_its_command_id(self) -> None:
        transcript = Transcript()
        patches = transcript.apply(_message("cmd-42", "user", "complete", "Run the tests"))

        self.assertEqual(patches[0]["item"]["id"], "m:cmd-42")
        self.assertEqual((patches[0]["item"]["role"], patches[0]["item"]["streaming"]), ("user", False))

    def test_a_tool_shows_its_title_output_and_result(self) -> None:
        transcript = Transcript()
        start = transcript.apply(_tool("t1", "start", text="uv run pytest"))
        first = transcript.apply(_tool("t1", "output", text="..F\n", stream="stdout"))
        more = transcript.apply(_tool("t1", "output", text="1 failed\n", stream="stdout"))
        done = transcript.apply(_tool("t1", "complete", exit_code=1, status="failed"))

        item = start[0]["item"]
        self.assertEqual((item["kind"], item["title"], item["status"]), ("command", "uv run pytest", "running"))
        self.assertEqual(first, [{"op": "append", "id": "t:t1", "field": "output", "text": "..F\n"}])
        self.assertEqual(more[0]["op"], "append")
        self.assertEqual((done[0]["item"]["status"], done[0]["item"]["exit_code"]), ("failed", 1))
        self.assertEqual(done[0]["item"]["output"], "..F\n1 failed\n")

    def test_a_tool_ends_as_its_status_says(self) -> None:
        transcript = Transcript()
        transcript.apply(_tool("t1", "complete", name="file_change", status="completed"))
        transcript.apply(_tool("t2", "complete", exit_code=0, status="completed"))
        transcript.apply(_tool("t3", "complete", name="mcp", status="failed"))
        transcript.apply(_tool("t4", "complete", status="declined"))

        self.assertEqual([item["status"] for item in transcript.items()], ["done", "done", "failed", "declined"])
        self.assertEqual([item["exit_code"] for item in transcript.items()], [None, 0, None, None])

    def test_error_output_alone_does_not_fail_a_tool(self) -> None:
        transcript = Transcript()
        transcript.apply(_tool("t1", "output", name="command", text="warning: deprecated\n", stream="stderr"))
        transcript.apply(_tool("t1", "complete", exit_code=0, status="completed"))
        # Without a status, a nonzero exit code is the only sign of failure.
        transcript.apply(_tool("t2", "complete", exit_code=2))

        self.assertEqual([item["status"] for item in transcript.items()], ["done", "failed"])
        self.assertTrue(transcript.items()[0]["stderr"])

    def test_a_tool_keeps_the_label_it_started_with(self) -> None:
        transcript = Transcript()
        transcript.apply(_tool("t1", "start", name="read"))
        transcript.apply(_tool("t1", "complete", name="edit", exit_code=0, status="completed"))

        self.assertEqual(transcript.items()[0]["kind"], "read")

    def test_long_output_keeps_its_end_and_says_so(self) -> None:
        transcript = Transcript()
        transcript.apply(_tool("t1", "start", text="make"))
        transcript.apply(_tool("t1", "output", text="a" * (MAX_OUTPUT_CHARS - 2)))
        patches = transcript.apply(_tool("t1", "output", text="bbbb"))

        item = transcript.items()[0]
        self.assertEqual(patches[0]["op"], "upsert")
        self.assertTrue(item["truncated"])
        self.assertEqual(len(item["output"]), MAX_OUTPUT_CHARS)
        self.assertTrue(item["output"].endswith("bbbb"))

    def test_output_past_the_bound_is_sent_as_a_trimmed_append(self) -> None:
        transcript = Transcript()
        transcript.apply(_tool("t1", "start", text="make"))
        transcript.apply(_tool("t1", "output", text="a" * MAX_OUTPUT_CHARS))
        first = transcript.apply(_tool("t1", "output", text="bb"))
        later = transcript.apply(_tool("t1", "output", text="cc"))

        self.assertEqual((first[0]["op"], first[0]["item"]["truncated"]), ("upsert", True))
        self.assertEqual(later, [{"op": "append", "id": "t:t1", "field": "output", "text": "cc", "trim": 2}])
        self.assertTrue(transcript.items()[0]["output"].endswith("bbcc"))

    def test_a_replacement_replaces_the_tool_output(self) -> None:
        transcript = Transcript()
        transcript.apply(_tool("t1", "output", text="Run three tock echoes"))
        replaced = transcript.apply(_tool("t1", "output", text="tock 1\n", replace=True))
        transcript.apply(_tool("t1", "output", text="tock 2\n"))

        self.assertEqual(replaced[0]["op"], "upsert")
        self.assertEqual(transcript.items()[0]["output"], "tock 1\ntock 2\n")

    def test_a_replayed_event_folds_once(self) -> None:
        transcript = Transcript()
        transcript.apply({**_tool("t1", "start", text="ls"), "sequence": 4})
        output = {**_tool("t1", "output", text="a.txt\n"), "sequence": 5}
        transcript.apply(output)
        again = transcript.apply(output)
        older = transcript.apply({**_tool("t1", "output", text="b.txt\n"), "sequence": 3})

        self.assertEqual((again, older), ([], []))
        self.assertEqual(transcript.items()[0]["output"], "a.txt\n")

    def test_a_notice_about_a_prompt_does_not_replace_it(self) -> None:
        transcript = Transcript()
        transcript.apply(_message("cmd-7", "user", "complete", "Steer this"))
        patches = transcript.apply(_message("cmd-7", "system", "complete", "Codex control request failed"))

        self.assertEqual(patches[0]["item"]["id"], "m:system:cmd-7")
        self.assertEqual([(item["role"], item["text"]) for item in transcript.items()],
                         [("user", "Steer this"), ("system", "Codex control request failed")])

    def test_what_never_finished_stops_when_the_turn_ends(self) -> None:
        transcript = Transcript()
        transcript.apply(_tool("t1", "start", text="sleep 60"))
        transcript.apply(_message("a1", "assistant", "delta", "Work"))
        patches = transcript.apply(_event("task_failed", {"message": "", "error_code": "interrupted"}, event_id="e9"))
        tool, message, turn = transcript.items()

        self.assertEqual([patch["item"]["id"] for patch in patches], ["t:t1", "m:a1", "r:e9"])
        self.assertEqual((tool["status"], message["streaming"], turn["status"]), ("stopped", False, "interrupted"))
        self.assertEqual(transcript.settle(), [])

    def test_error_output_marks_the_tool(self) -> None:
        transcript = Transcript()
        transcript.apply(_tool("t1", "start", text="ls missing"))
        patches = transcript.apply(_tool("t1", "output", text="No such file\n", stream="stderr"))

        self.assertEqual(patches[0]["op"], "upsert")
        self.assertTrue(patches[0]["item"]["stderr"])

    def test_requests_and_turn_results_become_items(self) -> None:
        transcript = Transcript()
        transcript.apply(_event("approval_request", {"request_id": "a-1", "summary": "Run git push", "expires_at": 9000},
                                correlation="a-1"))
        repeat = transcript.apply(_event("approval_request", {"request_id": "a-1", "summary": "Run git push",
                                                              "expires_at": 9000}, correlation="a-1"))
        transcript.apply(_event("user_input_required", {"prompt": "Which branch?"}, correlation="a-2"))
        transcript.apply(_event("task_completed", {"message": "", "error_code": ""}, event_id="e1"))
        transcript.apply(_event("task_failed", {"message": "", "error_code": "interrupted"}, event_id="e2"))
        transcript.apply(_event("task_failed", {"message": "stopped", "error_code": "usage_limit"}, event_id="e3"))

        items = transcript.items()
        self.assertEqual(repeat, [])
        self.assertEqual((items[0]["type"], items[0]["approval_id"], items[0]["expires_at"]), ("approval", "a-1", 9000))
        self.assertEqual((items[1]["type"], items[1]["prompt"]), ("input", "Which branch?"))
        self.assertEqual([(item["status"], item["code"]) for item in items[2:]],
                         [("completed", ""), ("interrupted", ""), ("failed", "usage_limit")])

    def test_an_approval_records_how_it_ended(self) -> None:
        transcript = Transcript()
        transcript.apply(_event("approval_request", {"request_id": "a-1", "summary": "Run", "expires_at": 9}, correlation="a-1"))
        ended = transcript.apply(_event("approval_resolved", {"request_id": "a-1", "by": "deskhelm", "decision": "reject",
                                                              "unknown": True}, correlation="a-1"))
        missing = transcript.apply(_event("approval_resolved", {"request_id": "a-9", "by": "native"}, correlation="a-9"))
        odd = transcript.apply(_event("approval_resolved", {"request_id": "a-1", "by": "magic"}, correlation="a-1"))
        item = transcript.items()[0]

        self.assertEqual(ended[0]["op"], "upsert")
        self.assertEqual((item["resolution"], item["decision"], item["uncertain"]), ("deskhelm", "reject", True))
        self.assertEqual((missing, odd), ([], []))

    def test_other_and_malformed_events_change_nothing(self) -> None:
        transcript = Transcript()

        self.assertEqual(transcript.apply(_event("attention", {"reason": "approval", "active": True})), [])
        self.assertEqual(transcript.apply({"kind": "message", "payload": "text"}), [])
        self.assertEqual(transcript.apply(_event("approval_request", {"summary": "no id"})), [])
        self.assertEqual(transcript.items(), [])

    def test_the_oldest_items_leave_when_a_bound_is_reached(self) -> None:
        by_count = Transcript(max_items=2)
        by_count.apply(_message("m1", "user", "complete", "one"))
        by_count.apply(_message("m2", "user", "complete", "two"))
        dropped = by_count.apply(_message("m3", "user", "complete", "three"))
        by_size = Transcript(max_chars=400)
        by_size.apply(_message("m1", "assistant", "complete", "x" * 200))
        trimmed = by_size.apply(_message("m2", "assistant", "complete", "y" * 200))

        self.assertEqual(dropped[-1], {"op": "remove", "id": "m:m1"})
        self.assertEqual([item["text"] for item in by_count.items()], ["two", "three"])
        self.assertEqual(trimmed[-1], {"op": "remove", "id": "m:m1"})
        self.assertEqual([item["id"] for item in by_size.items()], ["m:m2"])

    def test_the_preview_is_the_latest_message_or_tool(self) -> None:
        transcript = Transcript()
        self.assertEqual(transcript.preview(), "")
        transcript.apply(_tool("t1", "start", text="npm   test"))
        self.assertEqual(transcript.preview(), "npm test")
        transcript.apply(_message("a1", "assistant", "complete", "All\n  tests pass."))
        self.assertEqual(transcript.preview(), "All tests pass.")

    def test_patches_are_copies(self) -> None:
        transcript = Transcript()
        patches = transcript.apply(_message("a1", "assistant", "complete", "kept"))
        patches[0]["item"]["text"] = "changed"

        self.assertEqual(transcript.items()[0]["text"], "kept")

    def test_replaying_the_patches_rebuilds_the_items(self) -> None:
        transcript = Transcript(max_items=6)
        events = [
            _message("u1", "user", "complete", "Fix it"),
            _tool("t1", "start", text="pytest"),
            _message("a1", "assistant", "delta", "Look"),
            _tool("t1", "output", text="F\n"),
            _message("a1", "assistant", "delta", "ing"),
            _tool("t1", "output", text="x" * (MAX_OUTPUT_CHARS + 5)),
            _tool("t1", "output", text="tail", stream="stderr"),
            _tool("t1", "output", text="more"),
            _tool("t1", "complete", exit_code=1, status="failed"),
            _message("a1", "assistant", "complete", "Looking now."),
            _event("approval_request", {"request_id": "a-9", "summary": "Edit", "expires_at": 5}, correlation="a-9"),
            _message("u2", "user", "complete", "go"),
            _event("task_completed", {"message": "", "error_code": ""}, event_id="done"),
            _message("a2", "assistant", "delta", "next"),
        ]
        replayed: dict[str, dict] = {}
        for event in events:
            _replay(transcript.apply(event), replayed)

        self.assertEqual(list(replayed.values()), transcript.items())


if __name__ == "__main__":
    unittest.main()
