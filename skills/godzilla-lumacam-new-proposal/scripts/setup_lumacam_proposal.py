#!/usr/bin/env python3
"""Prepare a new LumaCam proposal and activate its acquisition and focus tools safely."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable


DEFAULT_REPOSITORY = Path("/home/localadmin/Programs/lumacam_measurementcontrol")
DEFAULT_DATA_BASE = Path("/data01")
DEFAULT_PHOTON_TEST_ROOT = Path("/data01/data_acquisition")
DEFAULT_BATCH_FOCUS_NOTEBOOK = Path(
	"/home/localadmin/Programs/tpx3cam-analysis/TPX3_batch_focus.ipynb"
)
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
EMAIL_ADDRESS = re.compile(r"^[^@\s,]+@[^@\s,]+$")


class SetupError(RuntimeError):
	"""Raised when setup cannot proceed without guessing or overwriting data."""


def resolved(path: Path) -> Path:
	return path.expanduser().resolve(strict=False)


def require_file(path: Path, label: str) -> Path:
	path = resolved(path)
	if not path.is_file():
		raise SetupError(f"{label} does not exist: {path}")
	return path


def require_directory(path: Path, label: str) -> Path:
	path = resolved(path)
	if not path.is_dir():
		raise SetupError(f"{label} does not exist: {path}")
	return path


def calibration_pair(calibration_dir: Path) -> tuple[Path, Path]:
	calibration_dir = require_directory(calibration_dir, "Calibration directory")
	pixel_candidates = sorted(path for path in calibration_dir.glob("*.bpc") if path.is_file())
	if len(pixel_candidates) != 1:
		raise SetupError(
			f"Expected exactly one .bpc pixel file in {calibration_dir}; "
			f"found {len(pixel_candidates)}"
		)
	pixel = pixel_candidates[0]
	paired_dacs = Path(str(pixel) + ".dacs")
	if paired_dacs.is_file():
		return pixel.resolve(), paired_dacs.resolve()
	dacs_candidates = sorted(path for path in calibration_dir.glob("*.dacs") if path.is_file())
	if len(dacs_candidates) != 1:
		raise SetupError(
			f"Expected {paired_dacs.name} or exactly one .dacs file in {calibration_dir}; "
			f"found {len(dacs_candidates)}"
		)
	return pixel.resolve(), dacs_candidates[0].resolve()


def recipient_addresses(raw_values: list[str]) -> tuple[str, ...]:
	addresses: list[str] = []
	for raw_value in raw_values:
		for raw_address in raw_value.split(","):
			address = raw_address.strip()
			if not address:
				continue
			if not EMAIL_ADDRESS.fullmatch(address):
				raise SetupError(f"Invalid monitored-acquisition recipient address: {address!r}")
			if address not in addresses:
				addresses.append(address)
	if not addresses:
		raise SetupError("At least one monitored-acquisition recipient email is required")
	return tuple(addresses)


def replace_assignment(text: str, key: str, value: str, path: Path) -> str:
	pattern = re.compile(rf"^(?P<prefix>{re.escape(key)}\s*=\s*).*$", re.MULTILINE)
	matches = list(pattern.finditer(text))
	if len(matches) != 1:
		raise SetupError(f"Expected exactly one {key} assignment in {path}; found {len(matches)}")
	match = matches[0]
	replacement = f"{match.group('prefix')}{value!r}"
	return text[: match.start()] + replacement + text[match.end() :]


def replace_marked_absolute_path(text: str, marker: str, value: Path, path: Path) -> str:
	lines = text.splitlines(keepends=True)
	line_indexes = [index for index, line in enumerate(lines) if marker in line]
	if len(line_indexes) != 1:
		raise SetupError(f"Expected exactly one {marker!r} request in {path}; found {len(line_indexes)}")
	index = line_indexes[0]
	line = lines[index]
	quoted_paths = [
		match
		for match in re.finditer(r"(?P<quote>['\"])(?P<path>/[^'\"]+)(?P=quote)", line)
		if not match.group("path").startswith("/config/load?")
	]
	if len(quoted_paths) != 1:
		raise SetupError(
			f"Expected exactly one calibration path on the {marker!r} line in {path}; "
			f"found {len(quoted_paths)}"
		)
	match = quoted_paths[0]
	quote = match.group("quote")
	lines[index] = line[: match.start()] + quote + str(value) + quote + line[match.end() :]
	return "".join(lines)


def atomic_write_text(path: Path, text: str) -> None:
	mode = path.stat().st_mode
	fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
	try:
		with os.fdopen(fd, "w", encoding="utf-8") as handle:
			handle.write(text)
			handle.flush()
			os.fsync(handle.fileno())
		os.chmod(temporary_name, mode)
		os.replace(temporary_name, path)
	except BaseException:
		try:
			os.unlink(temporary_name)
		except FileNotFoundError:
			pass
		raise


def photon_test_files(root: Path) -> list[Path]:
	root = require_directory(root, "Photon-test root")
	files = sorted(path.resolve() for path in root.rglob("tpxAcqPhotonTest.py") if path.is_file())
	if not files:
		raise SetupError(f"No tpxAcqPhotonTest.py files found below {root}")
	return files


def validate_python_text(text: str, path: Path) -> None:
	try:
		compile(text, str(path), "exec")
	except SyntaxError as error:
		raise SetupError(f"Generated Python is invalid for {path}: {error}") from error


def validate_shell_text(text: str, path: Path) -> None:
	try:
		result = subprocess.run(
			["bash", "-n"], input=text, capture_output=True, text=True, timeout=5
		)
	except (FileNotFoundError, subprocess.SubprocessError) as error:
		raise SetupError(f"Could not validate generated shell file {path}: {error}") from error
	if result.returncode != 0:
		raise SetupError(f"Generated shell is invalid for {path}: {result.stderr.strip()}")


def prepare_batch_focus_notebook_text(notebook_path: Path, proposal_dir: Path) -> str:
	"""Point the batch-focus notebook at this proposal's TPX3 root."""
	notebook_path = require_file(notebook_path, "Batch-focus notebook")
	try:
		payload = json.loads(notebook_path.read_text(encoding="utf-8"))
	except json.JSONDecodeError as error:
		raise SetupError(f"Invalid notebook JSON {notebook_path}: {error}") from error

	cells = payload.get("cells")
	if not isinstance(cells, list):
		raise SetupError(f"Notebook has no valid cells list: {notebook_path}")

	target = proposal_dir / "data/experiments/tpx3Files"
	assignments = {
		"series_dir": f"Path({str(target)!r})",
		"cache_dir": 'series_dir.parent / ".work" / "batch_focus_cache"',
		"output_csv": (
			'series_dir.parents[2] / "documentation" / "batch_focus_summary.csv"'
		),
	}
	match_counts = {key: 0 for key in assignments}
	for cell_index, cell in enumerate(cells):
		if not isinstance(cell, dict) or cell.get("cell_type") != "code":
			continue
		source = cell.get("source", [])
		source_text = "".join(source) if isinstance(source, list) else str(source)
		updated_text = source_text
		cell_changed = False
		for key, value in assignments.items():
			pattern = re.compile(
				rf"^(?P<prefix>{re.escape(key)}\s*=\s*).*$",
				re.MULTILINE,
			)
			updated_text, count = pattern.subn(
				lambda match, replacement=value: (
					f"{match.group('prefix')}{replacement}"
				),
				updated_text,
			)
			match_counts[key] += count
			cell_changed = cell_changed or bool(count)
		if not cell_changed:
			continue
		try:
			compile(updated_text, f"{notebook_path}:cell-{cell_index}", "exec")
		except SyntaxError as error:
			raise SetupError(
				f"Generated batch-focus cell is invalid in {notebook_path}: {error}"
			) from error
		cell["source"] = (
			updated_text.splitlines(keepends=True) if isinstance(source, list) else updated_text
		)

	invalid_counts = {key: count for key, count in match_counts.items() if count != 1}
	if invalid_counts:
		raise SetupError(
			f"Expected exactly one assignment for each of {tuple(assignments)} in "
			f"{notebook_path}; counts: {invalid_counts}"
		)
	return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def prepare_file_updates(
	repository: Path,
	photon_root: Path,
	proposal_dir: Path,
	pixel: Path,
	dacs: Path,
	batch_focus_notebook: Path,
	recipients: tuple[str, ...],
) -> dict[Path, str]:
	settings_path = require_file(repository / "python/settings_installation.py", "Installation settings")
	acquisition_path = require_file(repository / "acquisitionSettings.sh", "Acquisition settings")
	monitor_path = require_file(
		repository / "monitor_dataAcq_sumImages.py", "Monitored-acquisition script"
	)
	updates: dict[Path, str] = {}

	settings_text = settings_path.read_text(encoding="utf-8")
	settings_text = replace_assignment(settings_text, "config_pixel_path", str(pixel), settings_path)
	settings_text = replace_assignment(settings_text, "config_dacs_path", str(dacs), settings_path)
	validate_python_text(settings_text, settings_path)
	updates[settings_path] = settings_text

	acquisition_text = acquisition_path.read_text(encoding="utf-8")
	acquisition_text = replace_assignment(
		acquisition_text, "LUMACAM_PROPOSAL_DIR", str(proposal_dir), acquisition_path
	)
	validate_shell_text(acquisition_text, acquisition_path)
	updates[acquisition_path] = acquisition_text

	monitor_text = monitor_path.read_text(encoding="utf-8")
	monitor_text = replace_assignment(
		monitor_text, "DEFAULT_EMAIL_TO", ",".join(recipients), monitor_path
	)
	validate_python_text(monitor_text, monitor_path)
	updates[monitor_path] = monitor_text

	for photon_path in photon_test_files(photon_root):
		photon_text = photon_path.read_text(encoding="utf-8")
		photon_text = replace_marked_absolute_path(
			photon_text, "format=pixelconfig", pixel, photon_path
		)
		photon_text = replace_marked_absolute_path(photon_text, "format=dacs", dacs, photon_path)
		validate_python_text(photon_text, photon_path)
		updates[photon_path] = photon_text

	batch_focus_notebook = require_file(batch_focus_notebook, "Batch-focus notebook")
	updates[batch_focus_notebook] = prepare_batch_focus_notebook_text(
		batch_focus_notebook, proposal_dir
	)
	return updates


def validate_template(template: Path) -> Path:
	template = require_directory(template, "Proposal template")
	data_root = require_directory(template / "data/experiments", "Template data root")
	for name in ("tpx3Files", "final", "derived", "logs", "metadata", ".work"):
		require_directory(data_root / name, f"Template {name} directory")
	require_file(template / "documentation/experiment_log.md", "Experiment log")
	require_file(template / "documentation/measurement_protocol.md", "Measurement protocol")
	marker = data_root / "metadata/layout.json"
	if marker.exists():
		raise SetupError(f"The structural template must not contain layout.json: {marker}")
	return data_root


def validate_processing_parameters(repository: Path) -> Path:
	path = require_file(repository / "parameterSettings.json", "Processing parameters")
	try:
		json.loads(path.read_text(encoding="utf-8"))
	except json.JSONDecodeError as error:
		raise SetupError(f"Invalid processing-parameter JSON {path}: {error}") from error
	return path


def contains_all(path: Path, values: Iterable[str]) -> None:
	text = path.read_text(encoding="utf-8")
	missing = [value for value in values if value not in text]
	if missing:
		raise SetupError(f"Post-write validation failed for {path}; missing: {missing}")


def notebook_source_contains_all(path: Path, values: Iterable[str]) -> None:
	"""Validate source text after decoding notebook JSON escaping."""
	try:
		payload = json.loads(path.read_text(encoding="utf-8"))
	except json.JSONDecodeError as error:
		raise SetupError(f"Invalid notebook JSON {path}: {error}") from error
	cells = payload.get("cells")
	if not isinstance(cells, list):
		raise SetupError(f"Notebook has no valid cells list: {path}")
	source_text = "\n".join(
		"".join(cell.get("source", []))
		for cell in cells
		if isinstance(cell, dict) and isinstance(cell.get("source", []), list)
	)
	missing = [value for value in values if value not in source_text]
	if missing:
		raise SetupError(f"Post-write validation failed for {path}; missing: {missing}")


def detector_warnings() -> list[str]:
	warnings: list[str] = []
	try:
		result = subprocess.run(
			["ss", "-lunp"], check=False, capture_output=True, text=True, timeout=5
		)
		owners = [line.strip() for line in result.stdout.splitlines() if re.search(r":8192\b", line)]
		if owners:
			warnings.append("Detector UDP port 8192 is already in use: " + " | ".join(owners))
	except (FileNotFoundError, subprocess.SubprocessError) as error:
		warnings.append(f"Could not inspect detector UDP port 8192: {error}")
	return warnings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--proposal", required=True, help="New proposal directory name")
	parser.add_argument("--calibration-dir", required=True, type=Path)
	parser.add_argument(
		"--email-to",
		action="append",
		required=True,
		metavar="ADDRESS",
		help="Monitored-acquisition recipient; repeat or provide a comma-separated list",
	)
	parser.add_argument("--repository", type=Path, default=DEFAULT_REPOSITORY)
	parser.add_argument("--data-base", type=Path, default=DEFAULT_DATA_BASE)
	parser.add_argument("--photon-test-root", type=Path, default=DEFAULT_PHOTON_TEST_ROOT)
	parser.add_argument(
		"--batch-focus-notebook", type=Path, default=DEFAULT_BATCH_FOCUS_NOTEBOOK
	)
	parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing")
	return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
	args = parse_args(argv)
	try:
		if not SAFE_NAME.fullmatch(args.proposal):
			raise SetupError(
				"Proposal name must start with a letter or digit and contain only letters, "
				"digits, '.', '_', or '-'"
			)
		repository = require_directory(args.repository, "LumaCam repository")
		data_base = require_directory(args.data_base, "Proposal parent")
		photon_root = require_directory(args.photon_test_root, "Photon-test root")
		template = repository / "lumacam_proposal_template"
		validate_template(template)
		require_directory(
			template / "data/experiments/tpx3Files", "Template TPX3 data directory"
		)
		processing_path = validate_processing_parameters(repository)
		pixel, dacs = calibration_pair(args.calibration_dir)
		recipients = recipient_addresses(args.email_to)
		batch_focus_notebook = require_file(args.batch_focus_notebook, "Batch-focus notebook")
		proposal_dir = resolved(data_base / args.proposal)
		if proposal_dir.parent != data_base:
			raise SetupError(f"Proposal path escapes proposal parent: {proposal_dir}")
		if proposal_dir.exists():
			raise SetupError(f"Refusing to overwrite existing proposal directory: {proposal_dir}")
		updates = prepare_file_updates(
			repository,
			photon_root,
			proposal_dir,
			pixel,
			dacs,
			batch_focus_notebook,
			recipients,
		)

		print(f"Proposal: {proposal_dir}")
		print(f"Template: {template.resolve()}")
		print(f"Pixel calibration: {pixel}")
		print(f"DAC calibration: {dacs}")
		print(f"Monitored-acquisition recipients: {', '.join(recipients)}")
		print(f"Processing parameters (unchanged): {processing_path}")
		print(f"Batch-focus TPX3 root: {proposal_dir / 'data/experiments/tpx3Files'}")
		print(
			f"Batch-focus cache: "
			f"{proposal_dir / 'data/experiments/.work/batch_focus_cache'}"
		)
		for path in updates:
			print(f"Update: {path}")

		if args.dry_run:
			print("DRY RUN: validation passed; no files were changed.")
			return 0

		shutil.copytree(template, proposal_dir, copy_function=shutil.copy2)
		for path, text in updates.items():
			atomic_write_text(path, text)

		validate_template(proposal_dir)
		contains_all(repository / "python/settings_installation.py", (str(pixel), str(dacs)))
		contains_all(repository / "acquisitionSettings.sh", (str(proposal_dir),))
		monitor_path = repository / "monitor_dataAcq_sumImages.py"
		monitor_text = monitor_path.read_text(encoding="utf-8")
		expected_email_assignment = f"DEFAULT_EMAIL_TO = {','.join(recipients)!r}"
		if expected_email_assignment not in monitor_text:
			raise SetupError(
				f"Post-write validation failed for {monitor_path}; expected exactly: "
				f"{expected_email_assignment}"
			)
		for path in photon_test_files(photon_root):
			contains_all(path, (str(pixel), str(dacs)))
		notebook_source_contains_all(
			batch_focus_notebook,
			(
				str(proposal_dir / "data/experiments/tpx3Files"),
				'cache_dir = series_dir.parent / ".work" / "batch_focus_cache"',
				(
					'output_csv = series_dir.parents[2] / "documentation" / '
					'"batch_focus_summary.csv"'
				),
			),
		)
		print("Setup completed and post-write validation passed.")
		for warning in detector_warnings():
			print(f"WARNING: {warning}", file=sys.stderr)
		print(f"Run from: cd -P {repository}")
		print("Smoke test: ./dataAcq_single.sh 5 test001")
		return 0
	except (SetupError, OSError, shutil.Error) as error:
		print(f"ERROR: {error}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())
