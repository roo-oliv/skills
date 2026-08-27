#!/usr/bin/env python3
"""Red cases for otel_headers.py — a helper that prints anything but json breaks every session start.

`gh` is never invoked: `subprocess.run` is replaced by a fake that records the argv it was handed and
returns whatever the case needs (a key, a failure, a message, an exception).

    python3 -m unittest discover -s ci -p 'otel_headers_test.py'
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import otel_headers  # noqa: E402
import skills_config  # noqa: E402

CONFIG = (
    "# Agent skills config\n"
    "\n"
    "## Telemetry\n"
    "\n"
    "- **Endpoint:** `https://otlp.example.com`\n"
    "- **Protocol:** `http/protobuf`\n"
    "- **Key variable:** `MY_INTAKE_KEY`\n"
    "- **Key header:** `Authorization`\n"
    "- **Key repo:** `acme/backend`\n"
)


class Completed:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


class Fake:
    """Stands in for subprocess.run — records argv, returns a canned result or raises."""

    def __init__(self, returncode: int = 0, stdout: str = "", raises: Exception | None = None) -> None:
        self.returncode, self.stdout, self.raises = returncode, stdout, raises
        self.calls: list = []

    def __call__(self, command, **kwargs):  # noqa: ANN001 — a subprocess.run stand-in
        self.calls.append(command)
        if self.raises is not None:
            raise self.raises
        return Completed(self.returncode, self.stdout)


class Harness(unittest.TestCase):
    config_text: str | None = None

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="otel-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        if self.config_text is not None:
            path = os.path.join(self.repo, skills_config.CONFIG_PATH)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.config_text)
        self.config = skills_config.load(self.repo)

    def with_gh(self, fake: Fake) -> Fake:
        original = otel_headers.subprocess.run
        otel_headers.subprocess.run = fake
        self.addCleanup(setattr, otel_headers.subprocess, "run", original)
        return fake

    def run_main(self, fake: Fake) -> str:
        self.with_gh(fake)
        stdout, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = otel_headers.main(["--repo", self.repo])
            printed = sys.stdout.getvalue()
        finally:
            sys.stdout = stdout
        self.assertEqual(code, 0)
        return printed


class Defaults(Harness):
    def test_a_key_becomes_the_default_datadog_header(self) -> None:
        printed = self.run_main(Fake(stdout="abc123\n"))
        self.assertEqual(json.loads(printed), {"dd-api-key": "abc123"})

    def test_the_default_command_lets_gh_infer_the_repo(self) -> None:
        fake = self.with_gh(Fake(stdout="abc123\n"))
        otel_headers.headers(self.config)
        self.assertEqual(fake.calls[0], ["gh", "variable", "get", "CLAUDE_CODE_OTEL_API_KEY"])

    def test_gh_failing_prints_an_empty_object(self) -> None:
        self.assertEqual(json.loads(self.run_main(Fake(returncode=1, stdout=""))), {})

    def test_gh_missing_prints_an_empty_object(self) -> None:
        self.assertEqual(json.loads(self.run_main(Fake(raises=FileNotFoundError("gh")))), {})

    def test_a_message_instead_of_a_value_is_never_a_header(self) -> None:
        # `gh` prints human text on some paths; a key is one opaque token, never a sentence.
        self.assertEqual(json.loads(self.run_main(Fake(stdout="variable not found\n"))), {})
        self.assertEqual(json.loads(self.run_main(Fake(stdout="   \n"))), {})

    def test_stdout_is_always_one_json_object_on_one_line(self) -> None:
        printed = self.run_main(Fake(stdout="abc123"))
        self.assertEqual(len(printed.strip().split("\n")), 1)
        self.assertIsInstance(json.loads(printed), dict)


class Configured(Harness):
    config_text = CONFIG

    def test_variable_header_and_repo_all_come_from_the_config(self) -> None:
        fake = self.with_gh(Fake(stdout="xyz789\n"))
        self.assertEqual(otel_headers.headers(self.config), {"Authorization": "xyz789"})
        self.assertEqual(
            fake.calls[0], ["gh", "variable", "get", "MY_INTAKE_KEY", "--repo", "acme/backend"]
        )

    def test_the_endpoint_and_protocol_are_read_for_the_installer(self) -> None:
        self.assertEqual(self.config.otel_endpoint, "https://otlp.example.com")
        self.assertEqual(self.config.otel_protocol, "http/protobuf")


class NoConfig(Harness):
    def test_defaults_apply_with_no_config_file_at_all(self) -> None:
        self.assertFalse(self.config.present)
        self.assertEqual(self.config.otel_key_variable, "CLAUDE_CODE_OTEL_API_KEY")
        self.assertEqual(self.config.otel_key_header, "dd-api-key")
        self.assertIsNone(self.config.otel_key_repo)
        self.assertEqual(self.config.otel_endpoint, "http://localhost:4318")


if __name__ == "__main__":
    unittest.main()
