#!/usr/bin/env python3
"""Cases for wf_timeline.py — an instrument that reports the wrong number is worse than none.

A synthetic ``wf_<id>`` run directory is built in a temp dir: agent transcripts with the role
marker, one legacy (pre-marker) transcript, a malformed line, and two stages whose windows
OVERLAP — the shape a per-stage "sum of maxima" would over-count, which is exactly why the tool
reports each stage's window instead.

    python3 -m unittest discover -s ci -p 'wf_timeline_test.py'
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wf_timeline  # noqa: E402

BASE = "2026-09-04T10:00:00Z"


def stamp(minute, second=0):
    return "2026-09-04T10:{:02d}:{:02d}Z".format(minute, second)


def user_line(text, at):
    return {"type": "user", "timestamp": at, "message": {"content": [{"type": "text", "text": text}]}}


def assistant_line(at, model="claude-sonnet-5", tools=0, usage=None):
    content = [{"type": "text", "text": "thinking"}]
    content += [{"type": "tool_use", "name": "Bash", "input": {}} for _ in range(tools)]
    return {
        "type": "assistant",
        "timestamp": at,
        "message": {
            "model": model,
            "content": content,
            "usage": usage or {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 500,
            },
        },
    }


class WfTimelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.run = Path(self.tmp) / "wf_deadbeef"
        self.run.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_agent(self, name, lines, meta=None, junk=False):
        path = self.run / "agent-{}.jsonl".format(name)
        with open(path, "w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")
            if junk:
                handle.write("{not json at all\n\n")
        if meta is not None:
            with open(self.run / "agent-{}.meta.json".format(name), "w", encoding="utf-8") as handle:
                json.dump(meta, handle)
        return path

    def write_journal(self, started, result, failed=0):
        with open(self.run / "journal.jsonl", "w", encoding="utf-8") as handle:
            for _ in range(started):
                handle.write(json.dumps({"type": "started"}) + "\n")
            for _ in range(result):
                handle.write(json.dumps({"type": "result"}) + "\n")
            for _ in range(failed):
                handle.write(json.dumps({"type": "failed"}) + "\n")

    # -- role attribution -------------------------------------------------------------------

    def test_marker_gives_workflow_label_and_stage(self):
        workflow, label, stage = wf_timeline.role_of(
            "[deep-plan role: analyze:cells-2]\nRead the role file..."
        )
        self.assertEqual((workflow, label, stage), ("deep-plan", "analyze:cells-2", "analyze"))

    def test_implement_labels_map_to_their_stage(self):
        cases = {
            "setup": "setup",
            "wave-3.1": "implement",
            "recon-fix": "implement",  # a wave agent under another name
            "verify-1": "verify",
            "verify-plan": "verify",
            "verify-final-retry": "verify",
            "pr-author": "pr",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(wf_timeline.stage_of("implement", label), expected)

    def test_legacy_prompt_without_a_marker_still_attributes(self):
        workflow, label, stage = wf_timeline.role_of(
            "Read `.claude/skills/deep-plan/agents/refuter.md` and operate as the refuter for "
            "round 1. Your assigned attack LENS #3: PREMISE"
        )
        self.assertEqual(workflow, "deep-plan")
        self.assertEqual(label, "refute r1-3")
        self.assertEqual(stage, "refute")

    def test_unrecognized_prompt_is_kept_as_its_own_row(self):
        workflow, label, stage = wf_timeline.role_of("some other agent entirely")
        self.assertEqual((workflow, stage), ("unknown", "other"))
        self.assertEqual(label, "some other agent entirely")

    # -- aggregation ------------------------------------------------------------------------

    def build_deep_plan_run(self):
        # enumerate 0.0 -> 6.0 and analyze 0.0 -> 9.0 OVERLAP: one parallel() fires both.
        self.write_agent("a", [
            user_line("[deep-plan role: enumerate:matrix-columns]\nx", stamp(0)),
            assistant_line(stamp(6), tools=3),
        ], meta={"agentType": "general-purpose"})
        self.write_agent("b", [
            user_line("[deep-plan role: analyze:dimension-table]\nx", stamp(0)),
            assistant_line(stamp(9), model="claude-opus-5", tools=2),
        ])
        self.write_agent("c", [
            user_line("[deep-plan role: refute:r1-1]\nx", stamp(10)),
            assistant_line(stamp(14), model="claude-opus-5", tools=1),
        ], junk=True)
        self.write_journal(started=3, result=3)
        return wf_timeline.analyze_run(self.run)

    def test_run_totals_and_wall_clock(self):
        run = self.build_deep_plan_run()
        self.assertEqual(run["workflow"], "deep-plan")
        self.assertEqual(run["agent_count"], 3)
        self.assertAlmostEqual(run["wall_minutes"], 14.0)
        self.assertEqual(run["totals"]["turns"], 3)
        self.assertEqual(run["totals"]["tools"], 6)
        self.assertEqual(run["totals"]["cache_read"], 3000)

    def test_a_malformed_transcript_line_is_skipped_not_fatal(self):
        run = self.build_deep_plan_run()
        refuter = [a for a in run["agents"] if a["role"] == "refute:r1-1"][0]
        self.assertEqual(refuter["turns"], 1)
        self.assertEqual(refuter["model"], "opus")

    def test_overlapping_stages_show_overlapping_windows(self):
        run = self.build_deep_plan_run()
        by_stage = {row["stage"]: row for row in run["stages"]}
        enumerate_row, analyze_row = by_stage["enumerate"], by_stage["analyze"]
        # Both start at t=0 and their windows overlap — summing the per-stage maxima would
        # report 6 + 9 = 15 minutes of "critical path" for 9 minutes of wall-clock.
        self.assertAlmostEqual(enumerate_row["start_min"], 0.0)
        self.assertAlmostEqual(analyze_row["start_min"], 0.0)
        self.assertLess(analyze_row["start_min"], enumerate_row["end_min"])
        self.assertAlmostEqual(analyze_row["end_min"], 9.0)
        self.assertAlmostEqual(by_stage["refute"]["start_min"], 10.0)

    def test_a_serial_stage_has_a_window_wider_than_its_longest_agent(self):
        # implement's verify stage: a parallel pair, then a serial retry.
        self.write_agent("v1", [
            user_line("[implement role: verify-1]\nx", stamp(0)),
            assistant_line(stamp(3)),
        ])
        self.write_agent("vp", [
            user_line("[implement role: verify-plan]\nx", stamp(0)),
            assistant_line(stamp(2)),
        ])
        self.write_agent("v2", [
            user_line("[implement role: verify-2]\nx", stamp(20)),
            assistant_line(stamp(24)),
        ])
        run = wf_timeline.analyze_run(self.run)
        verify = [row for row in run["stages"] if row["stage"] == "verify"][0]
        self.assertAlmostEqual(verify["max_minutes"], 4.0)
        self.assertAlmostEqual(verify["end_min"] - verify["start_min"], 24.0)

    def test_an_unfinished_run_is_reported_as_in_flight(self):
        self.build_deep_plan_run()
        self.write_journal(started=5, result=3)
        run = wf_timeline.analyze_run(self.run)
        self.assertEqual(run["journal"]["in_flight"], 2)

    # -- CLI --------------------------------------------------------------------------------

    def test_json_output_is_serializable_and_carries_the_windows(self):
        self.build_deep_plan_run()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            status = wf_timeline.main([str(self.run), "--json"])
        self.assertEqual(status, 0)
        payload = json.loads(buffer.getvalue().strip())
        self.assertEqual(payload["run"], "wf_deadbeef")
        self.assertTrue(all("start_min" in row and "end_min" in row for row in payload["stages"]))
        self.assertIsInstance(payload["start"], str)

    def test_stages_table_prints_every_stage(self):
        self.build_deep_plan_run()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            wf_timeline.main([str(self.run), "--stages"])
        out = buffer.getvalue()
        for stage in ("enumerate", "analyze", "refute"):
            self.assertIn(stage, out)
        self.assertIn("start_min", out)

    def test_a_missing_directory_is_an_error_not_a_crash(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            status = wf_timeline.main([str(self.run / "nope")])
        self.assertEqual(status, 1)

    def test_a_run_directory_with_no_transcripts_is_an_error(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
            status = wf_timeline.main([str(self.run)])
        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
