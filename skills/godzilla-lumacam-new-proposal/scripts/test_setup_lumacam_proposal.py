"""Isolated proposal setup tests: no detector, live proposal, notebook, or email."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import setup_lumacam_proposal as setup


class ProposalSetupLayoutTests(unittest.TestCase):
    def fixture(self, base, spelling):
        repo = base / "repo"
        template = repo / "lumacam_proposal_template"
        for name in ("tpx3Files", "final", "derived", "logs", "metadata", ".work"):
            (template / "data" / spelling / name).mkdir(parents=True)
        (template / "documentation").mkdir()
        for name in ("experiment_log.md", "measurement_protocol.md"):
            (template / "documentation" / name).write_text("test")
        (repo / "python").mkdir()
        (repo / "python/settings_installation.py").write_text(
            "config_pixel_path = '/old/settings.bpc'\nconfig_dacs_path = '/old/settings.bpc.dacs'\n")
        (repo / "acquisitionSettings.sh").write_text("LUMACAM_PROPOSAL_DIR='/old/proposal'\n")
        (repo / "monitor_dataAcq_sumImages.py").write_text("DEFAULT_EMAIL_TO = 'old@example.org'\n")
        (repo / "parameterSettings.json").write_text('{}')
        photon = base / "photon"
        photon.mkdir()
        (photon / "tpxAcqPhotonTest.py").write_text(
            "requests.get('http://localhost/config/load?format=pixelconfig', params={'file': '/old/settings.bpc'})\n"
            "requests.get('http://localhost/config/load?format=dacs', params={'file': '/old/settings.bpc.dacs'})\n")
        calib = base / "calib"
        calib.mkdir()
        (calib / "settings.bpc").write_bytes(b'pixel')
        (calib / "settings.bpc.dacs").write_bytes(b'dacs')
        notebook = base / "focus.ipynb"
        notebook.write_text(json.dumps({"cells": [{"cell_type": "code", "source": [
            "from pathlib import Path\n", "series_dir = Path('/old')\n",
            "cache_dir = Path('/old/cache')\n", "output_csv = Path('/old/result.csv')\n"]}]}))
        data = base / "data"
        data.mkdir()
        args = ["--repository", str(repo), "--proposal", "campaign",
            "--data-base", str(data), "--calibration-dir", str(calib),
            "--email-to", "tester@example.org", "--photon-test-root", str(photon),
            "--batch-focus-notebook", str(notebook)]
        return repo, data / "campaign", notebook, args

    def test_setup_and_focus_paths_follow_actual_template_root(self):
        for spelling in ("experiment", "experiments"):
            with self.subTest(spelling=spelling), tempfile.TemporaryDirectory() as tmp:
                repo, proposal, notebook, args = self.fixture(Path(tmp), spelling)
                with patch.object(setup, "detector_warnings", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(setup.main(args), 0)
                root = proposal / "data" / spelling
                self.assertTrue((root / "tpx3Files").is_dir())
                self.assertEqual(setup.validate_template(proposal), root)
                cells = json.loads(notebook.read_text())["cells"]
                namespace = {}
                exec(''.join(cells[0]["source"]), namespace)
                self.assertEqual(namespace["series_dir"], root / "tpx3Files")
                self.assertEqual(namespace["cache_dir"], root / ".work/batch_focus_cache")
                self.assertEqual(namespace["output_csv"], proposal / "documentation/batch_focus_summary.csv")
                self.assertIn(str(proposal), (repo / "acquisitionSettings.sh").read_text())
                self.assertIn("tester@example.org", (repo / "monitor_dataAcq_sumImages.py").read_text())

    def test_ambiguous_template_refused_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, proposal, notebook, args = self.fixture(Path(tmp), "experiment")
            (repo / "lumacam_proposal_template/data/experiments").mkdir()
            before = notebook.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(setup.main(args), 1)
            self.assertFalse(proposal.exists())
            self.assertEqual(notebook.read_bytes(), before)

    def test_dry_run_does_not_change_settings_or_create_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, proposal, notebook, args = self.fixture(Path(tmp), "experiment")
            originals = {p: p.read_bytes() for p in (notebook, repo / "acquisitionSettings.sh",
                repo / "python/settings_installation.py", repo / "monitor_dataAcq_sumImages.py")}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(setup.main(args + ["--dry-run"]), 0)
            self.assertFalse(proposal.exists())
            self.assertEqual(originals, {p: p.read_bytes() for p in originals})


if __name__ == "__main__":
    unittest.main()
