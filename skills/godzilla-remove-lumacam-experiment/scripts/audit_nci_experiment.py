#!/usr/bin/env python3
"""Read-only audit of one experiment in the LumaCam/NCI proposal layout."""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path


SECTIONS = (
    ".work",
    "derived",
    "final",
    "logs",
    "metadata",
    "tpx3Files",
    "rawFiles",
    "photonFiles",
)


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(value)
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024.0
    raise AssertionError("unreachable")


def experiments_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.name == "experiments" and resolved.is_dir():
        return resolved
    candidate = resolved / "data" / "experiments"
    if candidate.is_dir():
        return candidate
    raise ValueError(
        f"Could not find data/experiments below proposal root: {resolved}"
    )


def validate_experiment_name(name: str) -> None:
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("Experiment must be one exact directory name, not a path")
    if any(character in name for character in "*?[]"):
        raise ValueError("Globs and partial experiment patterns are not allowed")


def directory_stats(path: Path) -> tuple[int, int, int]:
    file_count = 0
    apparent_bytes = 0
    allocated_bytes = 0
    for directory, directory_names, file_names in os.walk(path, followlinks=False):
        directory_names[:] = [
            name for name in directory_names if not (Path(directory) / name).is_symlink()
        ]
        for name in file_names:
            entry = Path(directory) / name
            try:
                stat = entry.lstat()
            except FileNotFoundError:
                continue
            file_count += 1
            apparent_bytes += stat.st_size
            allocated_bytes += stat.st_blocks * 512
    return file_count, apparent_bytes, allocated_bytes


def manifest_summary(root: Path, experiment: str) -> tuple[int | None, int | None, dict[str, int], str | None]:
    manifest = root / "metadata" / experiment / "experiment.json"
    if not manifest.is_file():
        return None, None, {}, "manifest not found"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        runs = data.get("runs", {})
        if not isinstance(runs, dict):
            raise ValueError("runs is not an object")
        statuses = collections.Counter(
            str(details.get("status", "unknown"))
            for details in runs.values()
            if isinstance(details, dict)
        )
        expected_runs = data.get("expected_runs", [])
        expected_count = len(expected_runs) if isinstance(expected_runs, list) else len(runs)
        return statuses.get("completed", 0), expected_count, dict(sorted(statuses.items())), None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return None, None, {}, f"could not read manifest: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proposal_root", type=Path)
    parser.add_argument("experiment")
    args = parser.parse_args()

    try:
        validate_experiment_name(args.experiment)
        root = experiments_root(args.proposal_root)
    except ValueError as error:
        parser.error(str(error))

    targets = []
    for section in SECTIONS:
        target = root / section / args.experiment
        if target.is_symlink():
            print(f"ERROR: refusing symlinked target: {target}", file=sys.stderr)
            return 4
        if target.is_dir():
            targets.append((section, target, *directory_stats(target)))

    completed, expected, statuses, manifest_error = manifest_summary(root, args.experiment)
    print(f"Proposal root: {root.parent.parent}")
    print(f"Experiments root: {root}")
    print(f"Experiment: {args.experiment}")
    if manifest_error:
        print(f"Completed runs: unavailable ({manifest_error})")
    else:
        print(f"Completed runs: {completed} / {expected}")
        print("Run statuses: " + ", ".join(f"{key}={value}" for key, value in statuses.items()))

    if not targets:
        print("Targets: none")
        print("Total: files=0, apparent=0.00 B, allocated=0.00 B")
        return 3

    total_files = total_apparent = total_allocated = 0
    print("Targets:")
    for section, target, files, apparent, allocated in targets:
        total_files += files
        total_apparent += apparent
        total_allocated += allocated
        print(
            f"  {section}: {target} | files={files}, "
            f"apparent={format_bytes(apparent)}, allocated={format_bytes(allocated)}"
        )
    print(
        f"Total: files={total_files}, apparent={format_bytes(total_apparent)}, "
        f"allocated={format_bytes(total_allocated)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
