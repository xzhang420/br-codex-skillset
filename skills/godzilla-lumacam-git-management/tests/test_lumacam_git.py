from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "lumacam_git.py"
SPEC = importlib.util.spec_from_file_location("lumacam_git", SCRIPT)
assert SPEC and SPEC.loader
lumacam = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lumacam)


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class RepositoryFixture:
    def __init__(self, root: Path):
        self.root = root
        self.live = root / "live"
        self.dev = root / "dev"
        self.remote = root / "remote.git"
        self.live.mkdir()
        git(self.live, "init", "-b", "main")
        git(self.live, "config", "user.name", "LumaCam Test")
        git(self.live, "config", "user.email", "lumacam@example.invalid")

        (self.live / "README.md").write_text("production\n", encoding="utf-8")
        (self.live / "parameterSettings.json").write_text("{}\n", encoding="utf-8")
        (self.live / "check.sh").write_text("#!/bin/bash\nset -u\n", encoding="utf-8")
        tests = self.live / "tests"
        tests.mkdir()
        (tests / "test_smoke.py").write_text(
            "import unittest\n\n"
            "class SmokeTest(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        git(self.live, "add", "README.md", "parameterSettings.json", "check.sh", "tests")
        git(self.live, "commit", "-m", "Initial production")
        self.remote.mkdir()
        git(self.remote, "init", "--bare")
        git(self.live, "remote", "add", "origin", str(self.remote))
        git(self.live, "push", "-u", "origin", "main")
        git(self.live, "branch", "develop")
        git(self.live, "worktree", "add", str(self.dev), "develop")
        git(self.dev, "push", "-u", "origin", "develop")

    def output(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def save_readme(self, text: str = "development\n") -> str:
        (self.dev / "README.md").write_text(text, encoding="utf-8")
        output = self.output("save")
        code = lumacam.main(
            [
                "save",
                "--message",
                "Update development behavior",
                "--path",
                "README.md",
                "--reviewed",
                "--live",
                str(self.live),
                "--dev",
                str(self.dev),
                "--output",
                str(output),
            ]
        )
        if code != 0:
            raise AssertionError(output.read_text(encoding="utf-8"))
        return json.loads(output.read_text(encoding="utf-8"))["commit_sha"]


class ParserTests(unittest.TestCase):
    def test_every_subcommand_requires_output(self) -> None:
        parser = lumacam.build_parser()
        commands = {
            "status": ["status"],
            "start": ["start", "--change", "example"],
            "test": ["test"],
            "save": ["save", "--message", "example", "--path", "README.md"],
            "promote": ["promote", "--source-sha", "abc", "--hardware-tested"],
            "rollback-plan": ["rollback-plan", "--tag", "production-2026-01-01.1"],
        }
        for name, arguments in commands.items():
            with self.subTest(name=name), self.assertRaises(SystemExit):
                parser.parse_args(arguments)

    def test_promote_requires_hardware_choice(self) -> None:
        parser = lumacam.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["promote", "--source-sha", "abc", "--output", "/tmp/x"])


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = RepositoryFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_status_reports_clean_synchronized_worktrees(self) -> None:
        output = self.fixture.output("status")
        with mock.patch.object(lumacam, "active_acquisition_processes", return_value=[]):
            code = lumacam.main(
                [
                    "status",
                    "--skip-fetch",
                    "--live",
                    str(self.fixture.live),
                    "--dev",
                    str(self.fixture.dev),
                    "--output",
                    str(output),
                ]
            )
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertTrue(payload["safe_to_start_new_change"])
        self.assertEqual(payload["branch_difference"]["develop_only_commits"], 0)

    def test_start_fast_forwards_develop_and_gives_next_instruction(self) -> None:
        output = self.fixture.output("start")
        code = lumacam.main(
            [
                "start",
                "--change",
                "Adjust image variants",
                "--live",
                str(self.fixture.live),
                "--dev",
                str(self.fixture.dev),
                "--output",
                str(output),
            ]
        )
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertIn("Edit only", payload["next_step"])
        self.assertEqual(git(self.fixture.dev, "status", "--porcelain"), "")

    def test_save_validates_commits_and_pushes_exact_reviewed_file(self) -> None:
        commit_sha = self.fixture.save_readme()
        remote_sha = git(self.fixture.remote, "rev-parse", "refs/heads/develop")
        self.assertEqual(commit_sha, remote_sha)
        self.assertEqual(git(self.fixture.dev, "status", "--porcelain"), "")

    def test_save_refuses_unlisted_change(self) -> None:
        (self.fixture.dev / "README.md").write_text("changed\n", encoding="utf-8")
        (self.fixture.dev / "extra.txt").write_text("unreviewed\n", encoding="utf-8")
        output = self.fixture.output("save-error")
        code = lumacam.main(
            [
                "save",
                "--message",
                "Incomplete review",
                "--path",
                "README.md",
                "--reviewed",
                "--live",
                str(self.fixture.live),
                "--dev",
                str(self.fixture.dev),
                "--output",
                str(output),
            ]
        )
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("exactly match", payload["error"])
        self.assertEqual(git(self.fixture.dev, "log", "-1", "--format=%s"), "Initial production")

    def test_skip_hardware_promotion_requires_exact_confirmation(self) -> None:
        commit_sha = self.fixture.save_readme()
        output = self.fixture.output("promote-error")
        code = lumacam.main(
            [
                "promote",
                "--source-sha",
                commit_sha,
                "--skip-hardware-test",
                "--confirmation",
                "yes",
                "--live",
                str(self.fixture.live),
                "--dev",
                str(self.fixture.dev),
                "--output",
                str(output),
            ]
        )
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertIn("exact confirmation", payload["error"])
        self.assertNotEqual(git(self.fixture.live, "rev-parse", "main"), commit_sha)

    def test_promote_fast_forwards_main_tags_and_pushes_atomically(self) -> None:
        commit_sha = self.fixture.save_readme()
        output = self.fixture.output("promote")
        with mock.patch.object(lumacam, "active_acquisition_processes", return_value=[]):
            code = lumacam.main(
                [
                    "promote",
                    "--source-sha",
                    commit_sha,
                    "--skip-hardware-test",
                    "--confirmation",
                    lumacam.SKIP_CONFIRMATION,
                    "--live",
                    str(self.fixture.live),
                    "--dev",
                    str(self.fixture.dev),
                    "--output",
                    str(output),
                ]
            )
        payload = json.loads(output.read_text(encoding="utf-8"))
        expected_tag = f"production-{date.today().isoformat()}.1"
        self.assertEqual(code, 0)
        self.assertEqual(payload["production_sha"], commit_sha)
        self.assertEqual(payload["production_tag"], expected_tag)
        self.assertEqual(git(self.fixture.live, "rev-parse", "main"), commit_sha)
        self.assertEqual(git(self.fixture.remote, "rev-parse", "refs/heads/main"), commit_sha)
        self.assertEqual(
            git(self.fixture.remote, "rev-parse", f"refs/tags/{expected_tag}^{{commit}}"),
            commit_sha,
        )

    def test_rollback_plan_does_not_move_main(self) -> None:
        commit_sha = self.fixture.save_readme()
        old_sha = git(self.fixture.live, "rev-parse", "main")
        tag = "production-2026-01-01.1"
        git(self.fixture.live, "tag", "-a", tag, old_sha, "-m", "Old production")
        git(self.fixture.live, "push", "origin", f"refs/tags/{tag}")
        output = self.fixture.output("rollback")
        with mock.patch.object(lumacam, "active_acquisition_processes", return_value=[]):
            code = lumacam.main(
                [
                    "rollback-plan",
                    "--tag",
                    tag,
                    "--live",
                    str(self.fixture.live),
                    "--dev",
                    str(self.fixture.dev),
                    "--output",
                    str(output),
                ]
            )
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertFalse(payload["mutated_repository"])
        self.assertEqual(git(self.fixture.live, "rev-parse", "main"), old_sha)
        self.assertEqual(git(self.fixture.dev, "rev-parse", "develop"), commit_sha)


if __name__ == "__main__":
    unittest.main()
