#!/usr/bin/env python3
"""Anonymous transcript fixtures; never read a developer's real Claude profile."""
from contextlib import closing
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import passport_claude_tokens as tokens


class ClaudeTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "claude"
        self.state = self.root / "state"
        self.now = int(dt.datetime(2026, 10, 11, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)

    def event(self, identifier="msg-a", at=None, **usage):
        stamp = dt.datetime.fromtimestamp((self.now - 1000 if at is None else at) / 1000, dt.timezone.utc).isoformat()
        return {"type": "assistant", "timestamp": stamp,
                "message": {"id": identifier, "model": "claude-example",
                "content": [{"text": "PRIVATE BODY MUST NOT PERSIST"}],
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30, **usage}}}

    def write(self, events, name="session.jsonl", profile=None):
        path = (profile or self.profile) / "projects/project" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
        return path

    def collect(self, **kwargs):
        return tokens.collect_claude_tokens(self.state, self.profile, self.now, **kwargs)

    def test_no_records_is_unknown(self):
        value = self.collect()
        self.assertFalse(value["available"])
        self.assertIsNone(value["today_tokens"])

    def test_disjoint_tokens_and_duplicate_content_blocks(self):
        event = self.event()
        event["message"]["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 20}
        self.write([event, event])
        value = self.collect()
        self.assertEqual((value["today_tokens"], value["lifetime_tokens"], value["call_count"]), (65, 65, 1))
        self.assertTrue(value["complete"])
        raw = next(self.state.glob("14 Claude Tokens *.json")).read_text()
        self.assertNotIn("PRIVATE BODY", raw)
        self.assertNotIn("msg-a", raw)

    def test_request_id_stream_updates_and_subagents(self):
        first, final = self.event(output_tokens=1), self.event(output_tokens=9)
        first["requestId"] = final["requestId"] = "req-a"
        final["message"]["id"] = "msg-other-block"
        self.write([first, final])
        self.write([final, self.event("msg-sub")], "session/subagents/agent-a.jsonl")
        self.assertEqual(self.collect()["lifetime_tokens"], 69 + 65)

    def test_optional_request_id_is_an_alias_of_the_same_api_message(self):
        first, copied = self.event(), self.event()
        first["requestId"] = "request-a"
        self.write([first, copied])
        value = self.collect()
        self.assertEqual((value["lifetime_tokens"], value["call_count"]), (65, 1))

    def test_historical_record_yields_real_zero_today(self):
        self.write([self.event(at=self.now - 3 * 86400000)])
        value = self.collect()
        self.assertTrue(value["available"])
        self.assertEqual((value["today_tokens"], value["lifetime_tokens"]), (0, 65))

    def test_partial_tail_preserves_old_observation(self):
        path = self.write([self.event()])
        old = self.collect()
        with path.open("a") as stream:
            stream.write(json.dumps(self.event("msg-b")))
        value = tokens.collect_claude_tokens(self.state, self.profile, self.now + 2000)
        self.assertFalse(value["available"])
        self.assertEqual(value["observed_at_ms"], old["observed_at_ms"])
        self.assertEqual(value["lifetime_tokens"], 65)
        with path.open("a") as stream:
            stream.write("\n")
        self.assertEqual(self.collect()["lifetime_tokens"], 130)

    def test_invalid_shapes_counts_and_future_time_are_incomplete(self):
        for bad in [self.event(input_tokens=True), self.event(output_tokens=-1),
                    self.event(cache_read_input_tokens=2**53), self.event(at=self.now + 1000),
                    {"type": "assistant", "message": []}]:
            with self.subTest(bad=bad):
                self.write([self.event("good"), bad])
                value = self.collect()
                self.assertFalse(value["available"])
                self.assertTrue(value["coverage"]["scan_incomplete"])

    def test_failed_api_synthetic_and_user_text_are_not_counters(self):
        failure = self.event("failed")
        failure["isApiErrorMessage"] = True
        synthetic = self.event("synthetic")
        synthetic["message"]["model"] = "<synthetic>"
        self.write([{"type": "user", "message": {"content": "usage"}}, failure, synthetic, self.event()])
        self.assertEqual(self.collect()["lifetime_tokens"], 65)

    def test_file_limit_and_byte_budget_are_incomplete(self):
        self.write([self.event()])
        self.write([self.event("other")], "other.jsonl")
        for index, arguments in enumerate(({"max_files": 1}, {"byte_budget": 1})):
            # Independent first-scan limits. Reusing one index across these
            # cases now deliberately resumes the preceding partial scan.
            value = tokens.collect_claude_tokens(self.state / str(index), self.profile, self.now, **arguments)
            self.assertFalse(value["available"])
            self.assertTrue(value["coverage"]["scan_incomplete"])

    def test_other_profiles_do_not_reuse_counter_cache(self):
        self.write([self.event()])
        self.assertTrue(self.collect()["available"])
        other = tokens.collect_claude_tokens(self.state, self.root / "other", self.now)
        self.assertIsNone(other["lifetime_tokens"])
        with mock.patch.dict(tokens.os.environ, {"CLAUDE_CONFIG_DIR": str(self.profile)}):
            self.assertEqual(tokens.collect_claude_tokens(self.state, now_ms=self.now)["lifetime_tokens"], 65)

    def test_symlink_transcript_never_read(self):
        target = self.write([self.event()])
        target.with_name("linked.jsonl").symlink_to(target)
        value = self.collect()
        self.assertFalse(value["available"])
        self.assertEqual(value["coverage"]["read_errors"], 1)

    def test_bad_duplicate_components_do_not_publish_an_invented_total(self):
        self.write([self.event(input_tokens=20, output_tokens=5), self.event(input_tokens=10, output_tokens=10)])
        self.assertFalse(self.collect()["available"])

    def test_tiny_budget_persists_progress_and_eventually_finishes(self):
        self.write([self.event(f"anonymous-{i}") for i in range(6)])
        offsets = []
        for _ in range(8):
            value = self.collect(byte_budget=1)
            offsets.append(value["coverage"]["indexed_bytes"])
            if value["available"]:
                break
        self.assertTrue(value["available"])
        self.assertEqual(value["lifetime_tokens"], 6 * 65)
        self.assertEqual(value["call_count"], 6)
        self.assertEqual(offsets, sorted(set(offsets)))
        cached = self.collect(byte_budget=1)
        self.assertEqual(cached["coverage"]["bytes_read"], 0)
        self.assertEqual(cached["coverage"]["verification_bytes"], 0)

    def test_file_work_limit_resumes_omitted_sources(self):
        self.write([self.event("first")], "first.jsonl")
        self.write([self.event("second")], "second.jsonl")
        self.assertFalse(self.collect(max_files=1)["available"])
        value = self.collect(max_files=1)
        self.assertTrue(value["available"])
        self.assertEqual(value["lifetime_tokens"], 130)

    def test_append_verifies_prefix_and_reads_only_new_records(self):
        path = self.write([self.event()])
        self.collect()
        old_size = path.stat().st_size
        added = json.dumps(self.event("second")) + "\n"
        with path.open("a") as stream:
            stream.write(added)
        value = self.collect()
        self.assertEqual(value["lifetime_tokens"], 130)
        self.assertEqual(value["coverage"]["bytes_read"], len(added.encode()))
        self.assertEqual(value["coverage"]["verification_bytes"], old_size)

    def test_same_inode_rewrite_larger_than_old_file_replaces_old_counters(self):
        path = self.write([self.event("old")])
        inode = path.stat().st_ino
        self.collect()
        self.write([self.event("new-a", input_tokens=20), self.event("new-b")])
        self.assertEqual(path.stat().st_ino, inode)
        value = self.collect()
        self.assertTrue(value["available"])
        self.assertEqual(value["lifetime_tokens"], 140)
        self.assertEqual(value["call_count"], 2)

    def test_same_size_edit_preserving_mtime_is_detected(self):
        path = self.write([self.event()])
        info = path.stat()
        self.collect()
        self.write([self.event(input_tokens=11)])
        self.assertEqual(path.stat().st_size, info.st_size)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        value = self.collect()
        self.assertTrue(value["available"])
        self.assertEqual(value["lifetime_tokens"], 66)

    def test_truncation_and_inode_replacement_rebuild_source(self):
        path = self.write([self.event("a"), self.event("b")])
        self.assertEqual(self.collect()["lifetime_tokens"], 130)
        self.write([self.event("c")])
        self.assertEqual(self.collect()["lifetime_tokens"], 65)
        replacement = path.with_name("replacement.tmp")
        replacement.write_text(json.dumps(self.event("d", input_tokens=25)) + "\n")
        replacement.replace(path)
        self.assertEqual(self.collect()["lifetime_tokens"], 80)

    def test_deleted_sources_are_removed_from_current_totals_and_index(self):
        first = self.write([self.event("a")], "first.jsonl")
        second = self.write([self.event("b")], "second.jsonl")
        self.assertEqual(self.collect()["lifetime_tokens"], 130)
        second.unlink()
        value = self.collect()
        self.assertTrue(value["available"])
        self.assertEqual(value["lifetime_tokens"], 65)
        first.unlink()
        self.assertFalse(self.collect()["available"])
        with closing(sqlite3.connect(next(self.state.glob("14 Claude Index *.sqlite3")))) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM counters").fetchone()[0], 0)

    def test_renamed_source_keeps_one_current_copy(self):
        path = self.write([self.event()])
        self.collect()
        path.rename(path.with_name("archived-copy.jsonl"))
        value = self.collect()
        self.assertTrue(value["available"])
        self.assertEqual((value["lifetime_tokens"], value["call_count"]), (65, 1))

    def test_request_message_aliases_merge_across_partial_refreshes(self):
        event = self.event()
        self.write([event], "one.jsonl")
        linked = json.loads(json.dumps(event))
        linked["requestId"] = "anonymous-request"
        self.write([linked], "two.jsonl")
        self.assertFalse(self.collect(max_files=1)["available"])
        value = self.collect(max_files=1)
        self.assertTrue(value["available"])
        self.assertEqual((value["lifetime_tokens"], value["call_count"]), (65, 1))

    def test_index_contains_no_plaintext_paths_message_ids_or_bodies(self):
        event = self.event()
        event["requestId"] = "private-request-marker"
        path = self.write([event])
        self.collect()
        database = next(self.state.glob("14 Claude Index *.sqlite3"))
        self.assertEqual(database.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(database)) as db:
            text = "\n".join(db.iterdump())
        for private in (str(path), str(self.profile), "msg-a", "private-request-marker", "PRIVATE BODY"):
            self.assertNotIn(private, text)

    def test_oversized_user_image_is_skipped_across_rounds_without_losing_later_counter(self):
        user = {"type": "user", "message": {"content": "anonymous-image-" + "x" * 4096}}
        self.write([user, self.event()])
        with mock.patch.object(tokens, "MAX_LINE", 512):
            for _ in range(80):
                value = self.collect(byte_budget=100)
                self.assertEqual(value["coverage"]["malformed_records"], 0)
                if value["available"]:
                    break
        self.assertTrue(value["available"])
        self.assertEqual(value["lifetime_tokens"], 65)

    def test_invalid_large_assistant_record_stays_uncertain(self):
        path = self.write([self.event()])
        old = self.collect()
        with path.open("a") as stream:
            stream.write(json.dumps({"type": "assistant", "message": "x" * 2048}) + "\n")
        with mock.patch.object(tokens, "MAX_LINE", 512):
            for _ in range(40):
                value = tokens.collect_claude_tokens(self.state, self.profile, self.now + 1000, byte_budget=100)
                if not value["coverage"]["pending_files"]:
                    break
        self.assertFalse(value["available"])
        self.assertEqual(value["coverage"]["malformed_records"], 1)
        self.assertEqual(value["observed_at_ms"], old["observed_at_ms"])

    def test_future_record_recovers_when_clock_catches_up_without_reparsing(self):
        self.write([self.event(at=self.now + 1000)])
        self.assertFalse(self.collect()["available"])
        value = tokens.collect_claude_tokens(self.state, self.profile, self.now + 2000)
        self.assertTrue(value["available"])
        self.assertEqual(value["lifetime_tokens"], 65)
        self.assertEqual(value["coverage"]["bytes_read"], 0)

    def test_default_256mib_budget_finishes_a_290mib_anonymous_image_across_calls(self):
        path = self.profile / "projects/anonymous/large-image.jsonl"
        path.parent.mkdir(parents=True)
        with path.open("wb") as stream:
            stream.write(b'{"type":"user","message":{"content":"')
            block = b"x" * (1 << 20)
            for _ in range(290):
                stream.write(block)
            stream.write(b'"}}\n')
            stream.write((json.dumps(self.event()) + "\n").encode())
        first = self.collect()
        self.assertFalse(first["available"])
        self.assertGreater(first["coverage"]["indexed_bytes"], 0)
        self.assertEqual(first["coverage"]["malformed_records"], 0)
        second = self.collect()
        self.assertTrue(second["available"])
        self.assertEqual(second["lifetime_tokens"], 65)
        self.assertLess(second["coverage"]["bytes_read"], 36 << 20)


if __name__ == "__main__":
    unittest.main()
