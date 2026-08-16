"""The .env loader, and the one property that makes it safe to use.

The interesting test here is not that KEY=VALUE parses. It is that a key which
arrives from the file is still scrubbed out of the run log, because Redactor
snapshots the environment when it is built and the loader is what has to run
first for that snapshot to contain anything.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forecaster.env import load_env
from forecaster.redact import Redactor


def write_env(text: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix="forecaster-env-")) / ".env"
    path.write_text(text, encoding="utf-8")
    return path


class LoadEnvTests(unittest.TestCase):
    def test_reads_pairs_and_ignores_noise(self) -> None:
        path = write_env(
            "# a comment\n"
            "\n"
            "ANTHROPIC_API_KEY=sk-ant-secret-value-here\n"
            "export EXA_API_KEY='quoted-value'\n"
            'LSE_BASE_URL="https://example.invalid/api"\n'
            "NOT_A_PAIR\n"
        )
        environ: dict[str, str] = {}
        report = load_env(path, environ)

        self.assertTrue(report.found)
        self.assertEqual(environ["ANTHROPIC_API_KEY"], "sk-ant-secret-value-here")
        self.assertEqual(environ["EXA_API_KEY"], "quoted-value")
        self.assertEqual(environ["LSE_BASE_URL"], "https://example.invalid/api")
        self.assertEqual(report.malformed, [6])

    def test_the_real_environment_wins(self) -> None:
        """A key already exported is never replaced by the file's copy."""
        path = write_env("ANTHROPIC_API_KEY=from-the-file\n")
        environ = {"ANTHROPIC_API_KEY": "from-the-shell"}
        report = load_env(path, environ)

        self.assertEqual(environ["ANTHROPIC_API_KEY"], "from-the-shell")
        self.assertEqual(report.already_set, ["ANTHROPIC_API_KEY"])
        self.assertEqual(report.loaded, [])

    def test_a_blank_existing_value_is_treated_as_unset(self) -> None:
        path = write_env("ANTHROPIC_API_KEY=from-the-file\n")
        environ = {"ANTHROPIC_API_KEY": "   "}
        load_env(path, environ)
        self.assertEqual(environ["ANTHROPIC_API_KEY"], "from-the-file")

    def test_missing_file_is_not_an_error(self) -> None:
        report = load_env(Path(tempfile.mkdtemp()) / "absent", {})
        self.assertFalse(report.found)
        self.assertIn("not present", report.describe())

    def test_describe_never_carries_a_value(self) -> None:
        path = write_env("ANTHROPIC_API_KEY=sk-ant-secret-value-here\n")
        environ: dict[str, str] = {}
        report = load_env(path, environ)
        rendered = report.describe() + " " + " ".join(report.loaded)
        self.assertNotIn("sk-ant-secret-value-here", rendered)


class RedactionOrderingTests(unittest.TestCase):
    """The reason load_env is called before RunLog is constructed."""

    def test_a_key_loaded_first_is_scrubbed(self) -> None:
        path = write_env("ANTHROPIC_API_KEY=sk-ant-loaded-from-the-file-000\n")
        environ: dict[str, str] = {}
        load_env(path, environ)

        redactor = Redactor(environ)
        line = "calling with key sk-ant-loaded-from-the-file-000 in the clear"
        self.assertNotIn("sk-ant-loaded-from-the-file-000", redactor(line))

    def test_a_key_loaded_afterwards_is_the_failure_this_ordering_avoids(self) -> None:
        """Build the redactor first and the same key survives -- hence the order."""
        path = write_env("SOME_PROVIDER_TOKEN=totally-unguessable-value-here\n")
        environ: dict[str, str] = {}

        redactor = Redactor(environ)  # snapshot taken while the file is unread
        load_env(path, environ)

        line = "token totally-unguessable-value-here"
        self.assertIn("totally-unguessable-value-here", redactor(line))


if __name__ == "__main__":
    unittest.main()
