"""Provider truthfulness, privacy selection, coverage and state TTL tests."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("passport_providers", Path(__file__).parents[1] / "tools/passport_providers.py")
providers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(providers)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.gemini = self.root / "gemini"
        self.cursor = self.root / "cursor.db"
        self.now = 2_000_000
        self.start = 1000

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, **kwargs):
        args = dict(cursor_db=self.cursor, gemini_home=self.gemini, now_ms=self.now)
        args.update(kwargs)
        return {p["provider"]: p for p in providers.load_provider_snapshots(self.start, self.state, **args)}

    def cursor_rows(self, rows):
        db = sqlite3.connect(self.cursor)
        db.execute("CREATE TABLE ai_code_hashes(requestId TEXT,timestamp INTEGER,model TEXT)")
        db.executemany("INSERT INTO ai_code_hashes VALUES(?,?,?)", rows)
        db.commit(); db.close()

    def session(self, name, messages, sid="session"):
        path = self.gemini / "tmp/project/chats" / ("session-" + name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sessionId": sid, "messages": messages}))
        return path

    def message(self, ident="message", at=1500, total=10, **changes):
        result = {"id": ident, "type": "gemini", "timestamp": providers.dt.datetime.fromtimestamp(at, providers.dt.timezone.utc).isoformat(),
                  "model": "gemini-pro", "tokens": {"total": total, "input": 7, "cached": 6, "output": 3},
                  "content": "PRIVATE RESPONSE", "thoughts": [{"text": "PRIVATE THOUGHT"}]}
        result.update(changes)
        return result

    def hook(self, provider="cursor", ident="a", state="working", at=None, until=None, group=None, **changes):
        at = self.now - 1000 if at is None else at
        item = {"provider": provider, "session_id_hash": ident * 64, "state": state,
                "state_at_ms": at, "state_until_ms": at + 180000 if until is None else until,
                "model": "model-small"}
        if group:
            item["session_group_hash"] = group * 64
        item.update(changes)
        path = self.state / "07 activity" / (provider + "-" + ident * 64 + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(item))
        return path

    def test_missing_sources_are_unknown_not_zero(self):
        for packet in self.collect().values():
            self.assertIsNone(packet["metric_value"])
            self.assertEqual(packet["metric_status"], "unavailable")
            self.assertEqual(packet["state"], "unknown")
            self.assertIsNone(packet["active_sessions"])
            self.assertEqual(packet["source"], "none")

    def test_cursor_distinct_requests_cycle_boundary_readonly(self):
        self.cursor_rows([("before",999000,"old"),("one",1000000,"m1"),
                          ("one",1200000,"m1"),("two",1900000,"m2")])
        before = self.cursor.read_bytes()
        result = self.collect()["cursor"]
        self.assertEqual(result["metric_value"],2)
        self.assertEqual(result["metric_kind"],"requests")
        self.assertEqual(result["metric_status"],"ready")
        self.assertEqual(result["state"],"unknown")
        self.assertEqual(result["metric_at_ms"],1900000)
        self.assertEqual(result["model"],"m2")
        self.assertEqual(self.cursor.read_bytes(),before)

    def test_cursor_uncertain_time_or_current_missing_id_is_partial(self):
        for bad in [("lost",None,"m"),(None,1500000,"m"),("",1500000,"m"),("future",2100000,"m")]:
            with self.subTest(bad=bad):
                self.cursor_rows([("one",1500000,"valid"),bad])
                result=self.collect()["cursor"]
                self.assertIsNone(result["metric_value"])
                self.assertEqual(result["metric_status"],"partial")
                self.cursor.unlink()

    def test_cursor_old_missing_id_does_not_pollute_current_cycle(self):
        self.cursor_rows([(None,500000,"old"),("current",1500000,"new")])
        self.assertEqual(self.collect()["cursor"]["metric_value"],1)

    def test_empty_cursor_vs_true_zero_current_cycle(self):
        self.cursor_rows([])
        self.assertEqual(self.collect()["cursor"]["metric_status"],"no_records")
        self.cursor.unlink()
        self.cursor_rows([("old",500000,"m")])
        value=self.collect()["cursor"]
        self.assertEqual((value["metric_value"],value["metric_status"]),(0,"ready"))

    def test_invalid_database_is_unavailable(self):
        self.cursor.write_bytes(b"not sqlite")
        self.assertEqual(self.collect()["cursor"]["metric_status"],"unavailable")

    def test_gemini_total_not_plus_cached_and_copied_session_dedup(self):
        messages=[self.message("old",999,100),self.message("one",1000,10),self.message("two",1900,20)]
        self.session("one",messages)
        self.session("duplicate",messages)
        value=self.collect()["gemini"]
        self.assertEqual(value["metric_value"],30)
        self.assertEqual(value["metric_status"],"ready")
        self.assertEqual(value["metric_at_ms"],1900000)
        self.assertEqual(value["model"],"gemini-pro")

    def test_gemini_message_ids_are_scoped_to_session(self):
        self.session("a",[self.message(total=10)],sid="a")
        self.session("b",[self.message(total=20)],sid="b")
        self.assertEqual(self.collect()["gemini"]["metric_value"],30)

    def test_gemini_no_model_records_is_not_zero_usage(self):
        self.session("user",[self.message(type="user",model="",tokens=None)])
        value=self.collect()["gemini"]
        self.assertEqual(value["metric_status"],"no_records")
        self.assertIsNone(value["metric_value"])
        self.assertEqual(value["last_activity_ms"],1500000)
        self.assertEqual(value["metric_at_ms"],0)

    def test_gemini_missing_counter_or_id_is_partial(self):
        for bad in [self.message(tokens=None),self.message(ident=""),self.message(total=True),
                    self.message(total=-1),self.message(total=providers.MAX_INTEGER+1),
                    self.message(timestamp="bad")]:
            with self.subTest(keys=list(bad)):
                path=self.session("bad",[bad])
                value=self.collect()["gemini"]
                self.assertEqual(value["metric_status"],"partial")
                self.assertIsNone(value["metric_value"])
                path.unlink()

    def test_conflicting_duplicate_counters_are_partial(self):
        self.session("a",[self.message(total=10)])
        self.session("b",[self.message(total=20)])
        self.assertEqual(self.collect()["gemini"]["metric_status"],"partial")

    def test_total_overflow_and_unknown_cycle_are_partial(self):
        self.session("a",[self.message("one",total=providers.MAX_INTEGER),self.message("two",total=1)])
        self.assertEqual(self.collect()["gemini"]["metric_status"],"partial")
        self.cursor_rows([("r",1500000,"m")])
        self.start=None
        self.assertEqual(self.collect()["cursor"]["metric_status"],"partial")

    def test_partial_json_and_scan_bounds_are_not_complete(self):
        path=self.session("a",[self.message()])
        path.write_text(path.read_text()[:-2])
        self.assertEqual(self.collect()["gemini"]["metric_status"],"partial")
        self.session("a",[self.message()])
        self.assertEqual(self.collect(gemini_byte_budget=10)["gemini"]["metric_status"],"partial")
        self.assertEqual(self.collect(max_gemini_files=0)["gemini"]["metric_status"],"partial")

    def test_gemini_symlinks_are_not_followed(self):
        path=self.session("original",[self.message()])
        path.with_name("session-copy.json").symlink_to(path)
        self.assertEqual(self.collect()["gemini"]["metric_status"],"partial")

    def test_gemini_permission_denied_is_unavailable_not_no_records(self):
        (self.gemini/"tmp").mkdir(parents=True)
        with mock.patch.object(providers.os,"scandir",side_effect=PermissionError):
            result=providers._gemini_metric(self.gemini,self.start*1000,self.now)
        self.assertEqual(result["metric_status"],"unavailable")
        self.assertIsNone(result["metric_value"])

    def test_selected_json_never_retains_content(self):
        value={"sessionId":"s","messages":[self.message(content="SECRET"*20000)],"credential":"SECRET"}
        result=providers._MetadataJSON(io.StringIO(json.dumps(value)),1000000).document(providers._GEMINI_FIELDS)
        self.assertNotIn("SECRET",json.dumps(result))
        self.assertNotIn("content",result["messages"][0])
        self.assertEqual(result["messages"][0]["tokens"],{"total":10})

    def test_selected_json_escape_boundaries_and_malformed_unknown_values(self):
        value={"skip":"x"*16382+'\\\"\n',"sessionId":"s","messages":[]}
        data=json.dumps(value)
        self.assertEqual(providers._MetadataJSON(io.StringIO(data),1000000).document(providers._GEMINI_FIELDS),{"sessionId":"s","messages":[]})
        for malformed in ['{"skip":"\\q"}', '{"skip":NaN}', '{"skip":[1,]}', '{"sessionId":"a","sessionId":"b"}', '{} extra']:
            with self.assertRaises(ValueError):
                providers._MetadataJSON(io.StringIO(malformed),10000).document(providers._GEMINI_FIELDS)

    def test_working_hooks_and_same_conversation_dedup(self):
        self.hook(ident="a",group="c")
        self.hook(ident="b",group="c")
        value=self.collect()["cursor"]
        self.assertEqual(value["state"],"working")
        self.assertEqual(value["active_sessions"],1)
        self.assertEqual(value["source"],"hooks")

    def test_state_priority_and_multiple_sessions(self):
        self.hook(ident="a",state="working")
        self.hook(ident="b",state="waiting")
        self.hook(ident="c",state="error")
        value=self.collect()["cursor"]
        self.assertEqual(value["state"],"waiting")
        self.assertEqual(value["active_sessions"],2)

    def test_expired_event_retains_original_clock_and_becomes_unknown(self):
        self.hook(at=self.now-180000)
        value=self.collect()["cursor"]
        self.assertEqual(value["state"],"unknown")
        self.assertEqual(value["state_until_ms"],self.now)
        self.assertEqual(value["state_at_ms"],self.now-180000)
        self.assertIsNone(value["active_sessions"])

    def test_old_terminal_does_not_poison_new_work_and_stale_active_hides_only_count(self):
        self.hook(ident="a",state="idle",at=self.now-200000)
        self.hook(ident="b",state="working")
        self.assertEqual(self.collect()["cursor"]["state"],"working")
        self.hook(ident="a",state="working",at=self.now-200000)
        value=self.collect()["cursor"]
        self.assertEqual(value["state"],"working")
        self.assertIsNone(value["active_sessions"])

    def test_all_stale_and_fresh_idle_plus_stale_working_remain_unknown(self):
        self.hook(ident="a",state="working",at=self.now-200000)
        self.assertEqual(self.collect()["cursor"]["state"],"unknown")
        self.hook(ident="b",state="idle")
        value=self.collect()["cursor"]
        self.assertEqual(value["state"],"unknown")
        self.assertIsNone(value["active_sessions"])

    def test_partial_other_session_keeps_positive_state_with_its_own_ttl(self):
        self.hook(ident="a",state="working",at=self.now-5000,until=self.now+60000)
        path=self.hook(ident="b",state="idle",at=self.now-1)
        path.write_text("{")
        value=self.collect()["cursor"]
        self.assertEqual(value["state"],"working")
        self.assertIsNone(value["active_sessions"])
        self.assertEqual((value["state_at_ms"],value["state_until_ms"]),(self.now-5000,self.now+60000))

    def test_fresh_waiting_or_error_not_hidden_by_unknown_sessions(self):
        for state in ("waiting","error"):
            self.hook(ident="a",state=state,at=self.now-5000,until=self.now+60000)
            self.hook(ident="b",state="unknown",at=self.now-1)
            value=self.collect()["cursor"]
            self.assertEqual(value["state"],state)
            self.assertIsNone(value["active_sessions"])
            self.assertEqual(value["state_until_ms"],self.now+60000)

    def test_future_extended_ttl_bad_identity_fail_closed(self):
        for change in [{"at":self.now+1},{"until":self.now+180000},
                       {"session_id_hash":"private-session"},{"state":"running"}]:
            with self.subTest(change=change):
                self.hook(**change)
                self.assertEqual(self.collect()["cursor"]["state"],"unknown")

    def test_bad_hook_does_not_hide_real_metric(self):
        self.cursor_rows([("r",1500000,"model")])
        path=self.hook();path.write_text("{")
        value=self.collect()["cursor"]
        self.assertEqual(value["metric_value"],1)
        self.assertEqual(value["state"],"unknown")
        self.assertEqual(value["source"],"cursor_code_tracking")

    def test_recollect_does_not_renew_source_times_or_expire_counters(self):
        self.cursor_rows([("r",1500000,"m")])
        self.hook()
        original=self.collect()["cursor"]
        self.now+=200000
        later=self.collect()["cursor"]
        self.assertEqual(later["state"],"unknown")
        self.assertEqual(later["state_until_ms"],original["state_until_ms"])
        self.assertEqual(later["metric_at_ms"],1500000)
        self.assertEqual(later["metric_value"],1)

    def test_model_ascii_bound_and_provider_isolation(self):
        self.hook(provider="gemini",model="私人中文")
        value=self.collect()
        self.assertEqual(value["gemini"]["model"],"")
        self.assertEqual(value["gemini"]["state"],"working")
        self.assertEqual(value["cursor"]["state"],"unknown")

    def test_codex_uses_supplied_authoritative_counter_and_metadata_only(self):
        db=sqlite3.connect(self.state/"01 usage.sqlite3")
        db.execute("CREATE TABLE events(at REAL,inherited INTEGER)")
        db.executemany("INSERT INTO events VALUES(?,?)",[(1500,0),(1900,1)]);db.commit();db.close()
        usage={"cycle_tokens":1234,"cycle_started_at":1000,"coverage":{"cycle_scan_incomplete":False}}
        value=self.collect(codex_usage=usage)["codex"]
        self.assertEqual((value["metric_value"],value["metric_at_ms"]),(1234,1500000))
        self.assertEqual(value["state"],"unknown")
        usage["coverage"]["cycle_scan_incomplete"]=True
        value=self.collect(codex_usage=usage)["codex"]
        self.assertEqual(value["metric_status"],"partial")
        self.assertIsNone(value["metric_value"])

    def test_unique_update_ids_and_exact_flat_schema(self):
        with mock.patch.object(providers.time,"time_ns",return_value=1000000):
            a=self.collect(); b=self.collect()
        self.assertGreater(b["codex"]["update_id"],a["codex"]["update_id"])
        expected={"provider","update_id","state","state_at_ms","state_until_ms","active_sessions",
                  "metric_kind","metric_value","metric_status","metric_at_ms","last_activity_ms","model","source"}
        for value in a.values():
            self.assertEqual(set(value),expected)
            self.assertTrue(providers._uint(value["update_id"]))
            self.assertEqual(value["update_id"],a["codex"]["update_id"])
            self.assertNotIn("quota",json.dumps(value))
            self.assertLessEqual(len(json.dumps(value,separators=(",",":")).encode()),512)

    def codex_log(self, name, events, sid=None, created=1000, archived=False):
        base=self.root/"codex"/("archived_sessions" if archived else "sessions")
        base.mkdir(parents=True,exist_ok=True)
        path=base/(name+".jsonl")
        iso=lambda second: providers.dt.datetime.fromtimestamp(second,providers.dt.timezone.utc).isoformat()
        rows=[{"type":"session_meta","payload":{"id":sid or name,"timestamp":iso(created),"cwd":"PRIVATE PATH"}}]
        for kind,turn,at in events:
            payload={"type":kind}
            if turn is not None:payload["turn_id"]=turn
            rows.append({"type":"event_msg","timestamp":iso(at),"payload":payload})
        path.write_text("".join(json.dumps(row)+"\n" for row in rows))
        os.utime(path,(self.now/1000,self.now/1000))
        return path

    def codex_activity(self, **kwargs):
        return providers._codex_activity(self.root/"codex",self.now,**kwargs)

    def test_codex_token_count_alone_never_means_working(self):
        self.codex_log("only-token",[("token_count",None,1999)])
        value=self.codex_activity()
        self.assertEqual(value["state"],"unknown")
        self.assertIsNone(value["active_sessions"])
        self.assertEqual(value["state_at_ms"],0)

    def test_codex_observed_start_allows_token_heartbeat(self):
        self.codex_log("active",[("task_started","t",1100),("token_count",None,1999)])
        value=self.codex_activity()
        self.assertEqual(value["state"],"working")
        self.assertEqual(value["active_sessions"],1)
        self.assertEqual(value["state_at_ms"],1999000)

    def test_codex_large_realistic_header_instructions_are_skipped(self):
        path=self.codex_log("active",[("task_started","t",1900)])
        lines=path.read_text().splitlines(keepends=True)
        header=json.loads(lines[0]); header["payload"]["base_instructions"]="PRIVATE"*3500
        lines[0]=json.dumps(header)+"\n";path.write_text("".join(lines))
        value=self.codex_activity()
        self.assertEqual(value["state"],"working")
        self.assertNotIn("PRIVATE",json.dumps(value))

    def test_codex_completed_sibling_does_not_replace_working_session(self):
        self.codex_log("a",[("task_started","t",1900)])
        self.codex_log("b",[("task_started","t",1901),("task_complete","t",1999)])
        value=self.codex_activity()
        self.assertEqual(value["state"],"working")
        self.assertEqual(value["active_sessions"],1)

    def test_codex_duplicate_archive_and_turn_ids(self):
        events=[("task_started","a",1900),("task_started","b",1901),("task_complete","a",1999)]
        self.codex_log("one",events,sid="same")
        self.codex_log("copy",events,sid="same",archived=True)
        value=self.codex_activity()
        self.assertEqual(value["state"],"working")
        self.assertEqual(value["active_sessions"],1)

    def test_codex_ambiguous_no_turn_heartbeat_is_unknown(self):
        self.codex_log("one",[("task_started","a",1100),("task_started","b",1101),("token_count",None,1999)])
        self.assertEqual(self.codex_activity()["state"],"unknown")

    def test_codex_abort_or_completion_requires_start(self):
        for terminal in ("task_complete","turn_aborted"):
            path=self.codex_log("one",[("task_started","a",1900),(terminal,"a",1999)])
            self.assertEqual(self.codex_activity()["state"],"idle")
            path.unlink()
            path=self.codex_log("one",[(terminal,"a",1999)])
            self.assertEqual(self.codex_activity()["state"],"unknown")
            path.unlink()

    def test_codex_expiry_and_inherited_history_do_not_fake_work(self):
        path=self.codex_log("one",[("task_started","a",1700)])
        self.assertEqual(self.codex_activity()["state"],"unknown")
        path.unlink()
        self.codex_log("one",[("task_started","old-parent",1000),("token_count",None,1999)],created=1800)
        self.assertEqual(self.codex_activity()["state"],"unknown")

    def test_codex_tail_missing_start_and_scan_budget_are_unknown(self):
        path=self.codex_log("one",[("task_started","a",1100),("token_count",None,1999)])
        lines=path.read_text().splitlines(keepends=True)
        lines.insert(2,json.dumps({"type":"response_item","payload":{"content":"PRIVATE"*3000}})+"\n")
        path.write_text("".join(lines))
        self.assertEqual(self.codex_activity(tail_bytes=256)["state"],"unknown")
        self.assertEqual(self.codex_activity(byte_budget=100)["state"],"unknown")
        self.assertEqual(self.codex_activity(max_files=0)["state"],"unknown")

    def test_codex_partial_terminal_record_is_unknown(self):
        path=self.codex_log("one",[("task_started","a",1900)])
        with path.open("a") as f:f.write('{"type":"event_msg","payload":')
        self.assertEqual(self.codex_activity()["state"],"unknown")

    def test_codex_fresh_state_integrates_without_changing_metric(self):
        self.codex_log("one",[("task_started","a",1900)])
        value=providers.load_provider_snapshots(self.start,self.state,self.root/"codex",
              cursor_db=self.cursor,gemini_home=self.gemini,now_ms=self.now)[0]
        self.assertEqual(value["state"],"working")
        self.assertEqual(value["source"],"codex_local")
        self.assertEqual(value["metric_status"],"unavailable")

    def codex_index(self, **kwargs):
        return providers._codex_index_activity(self.root/"codex",self.state,self.now,**kwargs)

    def codex_index_path(self):
        directory = providers._codex_profile_state(self.state, self.root / "codex")
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "09 codex activity.sqlite3"

    def test_codex_supplied_profile_time_does_not_read_default_profile_cache(self):
        path = self.state / "01 usage.sqlite3"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE events(at REAL,inherited INTEGER)")
        db.execute("INSERT INTO events VALUES(1999,0)")
        db.commit(); db.close()
        usage = {"cycle_tokens": 25, "cycle_started_at": self.start,
                 "last_token_event_at": 1500, "coverage": {}}
        value = self.collect(codex_usage=usage)["codex"]
        self.assertEqual(value["metric_at_ms"], 1500000)
        self.assertEqual(value["metric_value"], 25)
        # An explicitly empty profile must not inherit the default profile's
        # timestamp and be misrepresented as a freshly observed zero count.
        usage.update(cycle_tokens=0, last_token_event_at=None)
        value = self.collect(codex_usage=usage)["codex"]
        self.assertEqual(value["metric_status"], "no_records")
        self.assertEqual(value["metric_at_ms"], 0)

    def test_codex_profile_activity_databases_are_isolated_and_legacy_is_untouched(self):
        first = self.root / "codex"
        other = self.root / "other-codex"
        self.codex_log("one", [("task_started", "same-turn", 1900)])
        second = self.codex_log("two", [("task_started", "same-turn", 1900), ("task_complete", "same-turn", 1999)])
        destination = other / "sessions/two.jsonl"
        destination.parent.mkdir(parents=True)
        second.rename(destination)
        legacy = self.state / "09 codex activity.sqlite3"
        legacy.write_bytes(b"preserved previous derived cache")
        a = providers._codex_index_activity(first, self.state, self.now)
        b = providers._codex_index_activity(other, self.state, self.now)
        self.assertEqual((a["state"], b["state"]), ("working", "idle"))
        first_state = providers._codex_profile_state(self.state, first)
        other_state = providers._codex_profile_state(self.state, other)
        self.assertNotEqual(first_state, other_state)
        self.assertTrue((first_state / "09 codex activity.sqlite3").is_file())
        self.assertTrue((other_state / "09 codex activity.sqlite3").is_file())
        self.assertEqual(legacy.read_bytes(), b"preserved previous derived cache")
        self.assertEqual(providers._codex_index_activity(first, self.state, self.now)["state"], "working")

    def test_codex_profile_namespace_normalizes_path_aliases(self):
        source = self.root / "codex"
        source.mkdir()
        alias = self.root / "profile-alias"
        alias.symlink_to(source)
        self.assertEqual(providers._codex_profile_state(self.state, source),
                         providers._codex_profile_state(self.state, alias))

    def append_event(self,path,kind,turn,at):
        payload={"type":kind}
        if turn is not None:payload["turn_id"]=turn
        stamp=providers.dt.datetime.fromtimestamp(at,providers.dt.timezone.utc).isoformat()
        with path.open("a") as f:f.write(json.dumps({"type":"event_msg","timestamp":stamp,"payload":payload})+"\n")
        os.utime(path,(self.now/1000,self.now/1000))

    def test_index_long_task_survives_many_budgeted_scans_and_ends(self):
        path=self.codex_log("long",[("task_started","turn",1100)],sid="PRIVATE_SESSION")
        with path.open("a") as f:
            for i in range(140):
                f.write(json.dumps({"type":"response_item","payload":{"text":"PRIVATE_BODY"*550}})+"\n")
        self.append_event(path,"token_count",None,1999)
        self.assertGreater(path.stat().st_size,512*1024)
        value=self.codex_index(byte_budget=32768)
        self.assertEqual(value["state"],"unknown")
        for attempt in range(80):
            value=self.codex_index(byte_budget=32768)
            if value["state"]=="working":break
        self.assertEqual(value["state"],"working")
        self.assertGreater(attempt,5)
        self.assertEqual(value["state_at_ms"],1999000)
        self.assertEqual(self.codex_index(byte_budget=1024)["state"],"working")
        self.append_event(path,"task_complete","turn",1999.5)
        value=self.codex_index(byte_budget=4096)
        self.assertEqual((value["state"],value["active_sessions"]),("idle",0))
        self.append_event(path,"token_count",None,1999.8)
        self.assertEqual(self.codex_index()["state"],"idle")
        raw=(self.codex_index_path()).read_bytes()
        self.assertNotIn(b"PRIVATE_SESSION",raw)
        self.assertNotIn(b"PRIVATE_BODY",raw)
        self.assertNotIn(b"long.jsonl",raw)

    def test_index_new_unscanned_file_invalidates_known_activity(self):
        self.codex_log("one",[("task_started","a",1900)])
        self.assertEqual(self.codex_index()["state"],"working")
        self.codex_log("two",[("task_started","b",1990)])
        self.assertEqual(self.codex_index(byte_budget=0)["state"],"unknown")
        value=self.codex_index()
        self.assertEqual((value["state"],value["active_sessions"]),("working",2))

    def test_index_partial_other_file_does_not_hide_verified_positive(self):
        good=self.codex_log("verified",[("task_started","a",1900)])
        bad=self.codex_log("incomplete",[("task_started","b",1900)])
        with bad.open("a") as f:f.write('{"type":"event_msg","payload":')
        value=self.codex_index()
        self.assertEqual(value["state"],"working")
        self.assertIsNone(value["active_sessions"])
        self.assertEqual(value["state_at_ms"],1900000)

    def test_index_truncation_and_rotation_fail_closed_then_reindex(self):
        path=self.codex_log("one",[("task_started","long-turn",1900),("token_count",None,1950)])
        self.assertEqual(self.codex_index()["state"],"working")
        self.codex_log("one",[("task_started","new",1990)],sid="new-session")
        self.assertEqual(self.codex_index()["state"],"unknown")
        self.assertEqual(self.codex_index()["state"],"working")
        replacement=self.codex_log("replacement",[("task_started","x",1990)],sid="replacement-session")
        replacement.replace(path)
        self.assertEqual(self.codex_index()["state"],"unknown")
        self.assertEqual(self.codex_index()["state"],"working")

    def test_index_partial_append_waits_for_complete_newline(self):
        path=self.codex_log("one",[("task_started","t",1900)])
        self.assertEqual(self.codex_index()["state"],"working")
        line=json.dumps({"type":"event_msg","timestamp":"1970-01-01T00:33:19+00:00","payload":{"type":"task_complete","turn_id":"t"}})
        with path.open("a") as f:f.write(line[:-4])
        self.assertEqual(self.codex_index()["state"],"unknown")
        with path.open("a") as f:f.write(line[-4:]+"\n")
        self.assertEqual(self.codex_index()["state"],"idle")

    def test_index_later_new_turn_without_start_is_unknown(self):
        path=self.codex_log("one",[("task_started","old",1900),("task_complete","old",1990)])
        self.assertEqual(self.codex_index()["state"],"idle")
        self.append_event(path,"token_count","new-unobserved",1999)
        self.assertEqual(self.codex_index()["state"],"unknown")

    def test_index_deduplicates_archived_tasks_and_keeps_other_active(self):
        events=[("task_started","same-turn",1900)]
        self.codex_log("one",events,sid="same-session")
        self.codex_log("copy",events,sid="same-session",archived=True)
        self.codex_log("second",[("task_started","other",1900),("task_complete","other",1999)])
        value=self.codex_index()
        self.assertEqual((value["state"],value["active_sessions"]),("working",1))

    def test_index_fork_history_and_token_alone_are_never_working(self):
        self.codex_log("fork",[("task_started","inherited",1700),("token_count",None,1999)],created=1800)
        self.assertEqual(self.codex_index()["state"],"unknown")

    def test_index_refresh_uses_source_time_not_poll_time(self):
        self.codex_log("one",[("task_started","t",1900)])
        self.assertEqual(self.codex_index()["state"],"working")
        self.now=2080000
        value=self.codex_index()
        self.assertEqual(value["state"],"unknown")
        self.assertEqual(value["state_at_ms"],1900000)

    def test_index_new_started_supersedes_old_without_fabricating_completion(self):
        path=self.codex_log("one",[("task_started","old",1100),("task_started","current",1900),("token_count",None,1999)])
        value=self.codex_index()
        self.assertEqual((value["state"],value["active_sessions"]),("working",1))
        db=sqlite3.connect(self.codex_index_path())
        old=db.execute("SELECT ended,end_kind,superseded FROM tasks WHERE started=1100000").fetchone()
        self.assertEqual(old,(None,None,1900000))
        db.close()
        self.append_event(path,"task_complete","old",1999.1)
        self.assertEqual(self.codex_index()["state"],"working")
        self.append_event(path,"task_complete","current",1999.2)
        self.assertEqual(self.codex_index()["state"],"idle")

    def test_index_version_one_reindexes_only_own_activity_metadata(self):
        self.codex_log("one",[("task_started","current",1900)])
        db=sqlite3.connect(self.codex_index_path())
        db.executescript('''CREATE TABLE files(path_hash TEXT PRIMARY KEY,device INTEGER,inode INTEGER,
            size INTEGER,mtime INTEGER,offset INTEGER,sid TEXT,created INTEGER,history_gap INTEGER,anchor TEXT);
            CREATE TABLE tasks(path_hash TEXT,turn_hash TEXT,started INTEGER,activity INTEGER,ended INTEGER,end_kind TEXT,
            PRIMARY KEY(path_hash,turn_hash)); PRAGMA user_version=1;''')
        db.close()
        value=self.codex_index()
        self.assertEqual(value["state"],"working")
        db=sqlite3.connect(self.codex_index_path())
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],2);db.close()

    def test_index_utf8_offsets_survive_append(self):
        path=self.codex_log("one",[("task_started","t",1900)])
        with path.open("a") as f:
            for _ in range(5):f.write(json.dumps({"type":"response_item","payload":{"text":"私密🙂"*6000}},ensure_ascii=False)+"\n")
        self.append_event(path,"token_count",None,1999)
        self.assertEqual(self.codex_index()["state"],"working")
        self.append_event(path,"task_complete","t",1999.1)
        self.assertEqual(self.codex_index()["state"],"idle")

    def test_index_newer_event_waits_then_recovers_without_permanent_gap(self):
        path=self.codex_log("one",[("task_started","t",1900),("token_count",None,2000.1)])
        self.assertEqual(self.codex_index()["state"],"unknown")
        db=sqlite3.connect(self.codex_index_path())
        offset,size,gap=db.execute("SELECT offset,size,history_gap FROM files").fetchone();db.close()
        self.assertLess(offset,size)
        self.assertEqual(gap,0)
        self.now+=200
        self.assertEqual(self.codex_index()["state"],"working")


if __name__ == "__main__":
    unittest.main()
