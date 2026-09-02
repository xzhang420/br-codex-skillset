from __future__ import annotations

import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "scicat_batch_ingest.py"
SPEC = importlib.util.spec_from_file_location("scicat_batch_ingest", SCRIPT)
assert SPEC and SPEC.loader
scicat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scicat)


class LayoutTests(unittest.TestCase):
    def test_singular_and_plural_layouts_are_supported(self) -> None:
        for directory_name in ("experiment", "experiments"):
            with self.subTest(directory_name=directory_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "Example_P20261234"
                (root / "data" / directory_name / "tpx3Files").mkdir(parents=True)
                (root / "data" / directory_name / "metadata").mkdir()
                layout = scicat.locate_layout(root)
                self.assertEqual(layout["experiment"].name, directory_name)
                self.assertEqual(scicat.proposal_number(root), "20261234")

    def test_custom_pid_prefix_is_not_hard_coded(self) -> None:
        self.assertEqual(scicat.proposal_pid("P20261234", "99.1234/"), "99.1234/20261234")


class TimestampTests(unittest.TestCase):
    def test_actual_run_times_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "exp001" / "experiment.json"
            path.parent.mkdir()
            path.write_text(
                json.dumps(
                    {
                        "runs": {
                            "2": {"started_at": "2026-07-27T12:40:00Z", "ended_at": "2026-07-27T12:56:16Z"},
                            "1": {"started_at": "2026-07-27T12:26:04Z", "ended_at": "2026-07-27T12:35:00Z"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            start, end, sources = scicat.timestamp_range([path])
            self.assertTrue(start.startswith("2026-07-27T12:26:04"))
            self.assertTrue(end.startswith("2026-07-27T12:56:16"))
            self.assertEqual(sources, ["exp001"])

    def test_split_archive_uses_only_its_declared_run_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata = Path(temporary) / "metadata" / "exp004"
            metadata.mkdir(parents=True)
            (metadata / "experiment.json").write_text(
                json.dumps(
                    {
                        "runs": {
                            "exp004_00000": {
                                "started_at": "2026-07-21T01:00:00Z",
                                "ended_at": "2026-07-21T01:10:00Z",
                            },
                            "exp004_00001": {
                                "started_at": "2026-07-21T02:00:00Z",
                                "ended_at": "2026-07-21T02:10:00Z",
                            },
                            "exp004_00002": {
                                "started_at": "2026-07-21T03:00:00Z",
                                "ended_at": "2026-07-21T03:10:00Z",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            archive = Path(temporary) / "exp004_part1_00000-00001.tar.gz"
            start, end, sources, warning = scicat.archive_times(Path(temporary) / "metadata", archive)
            self.assertTrue(start.startswith("2026-07-21T01:00:00"))
            self.assertTrue(end.startswith("2026-07-21T02:10:00"))
            self.assertEqual(sources, ["exp004_00000", "exp004_00001"])
            self.assertIsNone(warning)

    def test_incomplete_acquisition_uses_latest_archived_tpx3_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "metadata" / "exp130"
            metadata.mkdir(parents=True)
            (metadata / "experiment.json").write_text(
                json.dumps(
                    {
                        "status": "in_progress",
                        "runs": {
                            "exp130_00000": {
                                "started_at": "2026-07-25T13:13:32Z",
                                "status": "acquiring",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            archive = root / "exp130.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                member = tarfile.TarInfo("exp130/exp130_00000/sample.tpx3")
                member.size = 1
                member.mtime = 1784985225  # 2026-07-25T13:13:45Z
                handle.addfile(member, io.BytesIO(b"x"))
            start, end, sources, warning = scicat.archive_times(root / "metadata", archive)
            self.assertEqual(start, "2026-07-25T13:13:32.000000Z")
            self.assertEqual(end, "2026-07-25T13:13:45.000000Z")
            self.assertEqual(sources, ["exp130"])
            self.assertIn("latest archived .tpx3 member mtime", warning or "")


class RemainingInventoryTests(unittest.TestCase):
    def test_raw_and_transient_content_is_excluded_but_scientific_content_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {
                "analysis/result.tif": b"result",
                "metadata/info.json": b"{}",
                "data/experiment/tpx3Files/exp001.tar.gz": b"raw",
                ".trash/deleted.bin": b"trash",
                "analysis/__pycache__/module.pyc": b"cache",
                "analysis/pending.partial": b"partial",
                "analysis/.gitkeep": b"",
            }
            for relative, payload in paths.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            inventory = scicat.inventory_remaining(root, "experiment")
            names = [item["path"] for item in inventory]
            self.assertEqual(names, ["analysis/result.tif", "metadata/info.json"])

    def test_symlinks_require_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("x", encoding="utf-8")
            os.symlink(target, root / "link.txt")
            with self.assertRaises(scicat.BatchError):
                scicat.inventory_remaining(root, "experiment")


class LifecycleTests(unittest.TestCase):
    def test_tape_confirmation_requires_status_and_retrievable(self) -> None:
        self.assertTrue(
            scicat.is_on_tape(
                {"datasetlifecycle": {"archiveStatusMessage": "datasetOnArchive", "retrievable": True}}
            )
        )
        self.assertFalse(
            scicat.is_on_tape(
                {"datasetlifecycle": {"archiveStatusMessage": "datasetOnArchive", "retrievable": False}}
            )
        )
        self.assertFalse(
            scicat.is_on_tape(
                {"datasetlifecycle": {"archiveStatusMessage": "workInProgress", "retrievable": True}}
            )
        )

    def test_historical_status_spelling_is_accepted_only_with_retrievability(self) -> None:
        self.assertTrue(
            scicat.is_on_tape(
                {"datasetlifecycle": {"archiveStatusMessage": "datasetOnAchive", "retrievable": True}}
            )
        )


class ApiPolicyTests(unittest.TestCase):
    def test_http_429_stops_with_dedicated_error(self) -> None:
        client = scicat.SciCatClient(mock.Mock(get=mock.Mock(return_value="token")), "https://example.invalid")
        client.limiter.wait = mock.Mock()
        error = urllib.error.HTTPError(
            "https://example.invalid/datasets", 429, "too many", {}, None
        )
        error.read = mock.Mock(return_value=b"slow down")
        with mock.patch.object(scicat.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(scicat.RateLimitError):
                client.request("GET", "/datasets")
        self.assertEqual(client.limiter.wait.call_count, 1)

    def test_http_500_uses_bounded_retry(self) -> None:
        client = scicat.SciCatClient(mock.Mock(get=mock.Mock(return_value="token")), "https://example.invalid")
        client.limiter.wait = mock.Mock()
        error = urllib.error.HTTPError(
            "https://example.invalid/datasets", 503, "unavailable", {}, None
        )
        error.read = mock.Mock(return_value=b"maintenance")
        with mock.patch.object(scicat.urllib.request, "urlopen", side_effect=error), mock.patch.object(
            scicat.time, "sleep"
        ) as sleeper:
            with self.assertRaises(scicat.BatchError) as raised:
                client.request("GET", "/datasets")
        self.assertIn("maintenance", str(raised.exception))
        self.assertEqual(client.limiter.wait.call_count, 5)
        self.assertEqual(sleeper.call_count, 4)


class DatasetPlanningTests(unittest.TestCase):
    def test_remaining_versions_never_replace_an_existing_dataset(self) -> None:
        datasets = [
            {"datasetName": "P20261234_remaining_processed_and_supporting_content_v001"},
            {"datasetName": "P20261234_remaining_processed_and_supporting_content_v003"},
        ]
        self.assertEqual(
            scicat.next_remaining_name("20261234", datasets),
            "P20261234_remaining_processed_and_supporting_content_v004",
        )

    def test_every_planned_raw_dataset_must_resolve_once(self) -> None:
        plan = {
            "rawEntries": [
                {
                    "datasetName": "P20261234_exp001",
                    "proposalId": "20.500.11935/20261234",
                    "ownerGroup": "p99999",
                    "sourceFolder": "/data01/proposal/data/experiment/tpx3Files",
                    "size": 10,
                }
            ]
        }
        remote = dict(plan["rawEntries"][0], pid="20.500.11935/example")
        self.assertEqual(scicat.resolve_raw_remotes(plan, [remote])[0]["pid"], remote["pid"])
        with self.assertRaises(scicat.BatchError):
            scicat.resolve_raw_remotes(plan, [])


class CliTests(unittest.TestCase):
    def test_all_subcommands_require_output(self) -> None:
        parser = scicat.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["status", "--plan", "plan.json"])

    def test_three_user_modes_are_available(self) -> None:
        parser = scicat.build_parser()
        for mode in ("all", "raw", "remaining"):
            args = parser.parse_args(
                ["prepare", "--mode", mode, "--proposal-root", "/data01/example", "--output", "/tmp/plan.json"]
            )
            self.assertEqual(args.mode, mode)

    def test_external_raw_source_is_available(self) -> None:
        parser = scicat.build_parser()
        args = parser.parse_args(
            [
                "prepare",
                "--mode",
                "raw",
                "--proposal-root",
                "/data01/example",
                "--raw-source-dir",
                "/media/example/tpx3Files",
                "--output",
                "/tmp/plan.json",
            ]
        )
        self.assertEqual(args.raw_source_dir, "/media/example/tpx3Files")


if __name__ == "__main__":
    unittest.main()
