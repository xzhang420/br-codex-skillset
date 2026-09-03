#!/usr/bin/env python3
"""Restart-safe LumaCam proposal ingestion for the PSI SciCat production catalog.

Tokens are requested with hidden input and retained only in process memory.  The
tool never deletes local data or SciCat records and stops on the first failure.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import getpass
import hashlib
import json
import os
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Iterator


DEFAULT_API_URL = "https://dacat.psi.ch/api/v3"
DEFAULT_ARCHIVE_HOST = "pb-archive.psi.ch"
DEFAULT_PID_PREFIX = "20.500.11935/"
DEFAULT_RATE_LOCK = Path("/tmp/godzilla-scicat-api-rate.lock")
DATASET_PID_RE = re.compile(r"Dataset created:\s*([^\s]+)")
JOB_ID_RE = re.compile(
    r"(?mi)^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
RSYNC_PROGRESS_RE = re.compile(
    r"(?P<bytes>[\d,]+)\s+(?P<percent>\d{1,3})%\s+(?P<rate>\S+/s)\s+(?P<eta>\d+:\d{2}:\d{2})"
)
TAPE_STATES = {"datasetonarchive", "datasetonachive"}
ARCHIVE_SUBMITTED_STATES = {
    "schedulearchivejob",
    "workinprogress",
    "datasetonarchive",
    "datasetonachive",
}
TRANSIENT_DIRS = {
    ".trash",
    ".work",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    "__pycache__",
    "cache",
    "caches",
    "tmp",
    "temp",
}
TRANSIENT_SUFFIXES = ("~", ".tmp", ".temp", ".part", ".partial", ".swp", ".swo", ".lock")
MAX_DATASET_BYTES = 50 * 10**12
MAX_DATASET_FILES = 400_000
RECOMMENDED_MAX_BYTES = 10**12


class BatchError(RuntimeError):
    """Expected, actionable workflow failure."""


class RateLimitError(BatchError):
    """SciCat returned HTTP 429; retrying automatically could worsen load."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def write_control_json(path: Path, value: Any) -> None:
    if path.exists():
        if read_json(path) != value:
            raise BatchError(f"Refusing to overwrite a changed control file: {path}")
        return
    atomic_json(path, value)


def write_control_text(path: Path, value: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != value:
            raise BatchError(f"Refusing to overwrite a changed control file: {path}")
        return
    atomic_text(path, value)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"Could not read JSON file {path}: {exc}") from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class TokenProvider:
    """Prompt for a SciCat token and keep it only in this process's memory."""

    def __init__(self) -> None:
        self._token = ""

    @staticmethod
    def expiry(token: str) -> int | None:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return int(json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))["exp"])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def get(self, minimum_valid_seconds: int = 600) -> str:
        expiry = self.expiry(self._token) if self._token else None
        if not self._token or (expiry is not None and expiry <= time.time() + minimum_valid_seconds):
            if self._token:
                print("SciCat token expired or expires soon; paste a fresh token.")
            self._token = getpass.getpass("Paste SciCat token (input hidden): ").strip()
        if not self._token:
            raise BatchError("No SciCat token was supplied.")
        return self._token


class RequestLimiter:
    """Cross-process one-request-per-second ceiling using a locked monotonic timestamp."""

    def __init__(self, lock_path: Path = DEFAULT_RATE_LOCK, seconds: float = 1.0) -> None:
        self.lock_path = lock_path
        self.seconds = seconds

    def wait(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="ascii") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            try:
                previous = float(handle.read().strip() or "0")
            except ValueError:
                previous = 0.0
            delay = self.seconds - (time.monotonic() - previous)
            if delay > 0:
                time.sleep(delay)
            handle.seek(0)
            handle.truncate()
            handle.write(f"{time.monotonic():.9f}")
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)


class SciCatClient:
    def __init__(self, token_provider: TokenProvider, base_url: str) -> None:
        self.tokens = token_provider
        self.base_url = base_url.rstrip("/")
        self.limiter = RequestLimiter()

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        last_network_error: Exception | None = None
        for attempt in range(5):
            self.limiter.wait()
            request = urllib.request.Request(url, data=payload, method=method)
            request.add_header("Authorization", "Bearer " + self.tokens.get())
            request.add_header("Accept", "application/json")
            if payload is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read()
                return json.loads(data) if data else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429:
                    raise RateLimitError(
                        f"SciCat returned HTTP 429 for {method} {path}: {detail}. "
                        "Stop and retry later; the tool will not increase request load."
                    ) from exc
                if 500 <= exc.code < 600 and attempt < 4:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise BatchError(f"SciCat {method} {path} returned HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                last_network_error = exc
                if attempt < 4:
                    time.sleep(min(2**attempt, 30))
                    continue
        raise BatchError(f"SciCat request failed after retries: {last_network_error}")

    def proposal(self, proposal_id: str) -> dict[str, Any]:
        result = self.request(
            "GET",
            "/proposals",
            query={"filters": json.dumps({"where": {"proposalId": proposal_id}}, separators=(",", ":"))},
        )
        if not isinstance(result, list) or len(result) != 1:
            raise BatchError(f"Expected one accessible SciCat proposal for {proposal_id}; found {len(result or [])}.")
        return result[0]

    def proposal_datasets(self, proposal_id: str) -> list[dict[str, Any]]:
        result = self.request(
            "GET",
            "/datasets",
            query={"filter": json.dumps({"where": {"proposalId": proposal_id}}, separators=(",", ":"))},
        )
        if not isinstance(result, list):
            raise BatchError("SciCat returned an unexpected dataset-list response.")
        return result

    def datasets_by_pids(self, pids: list[str]) -> list[dict[str, Any]]:
        if not pids:
            return []
        result = self.request(
            "GET",
            "/datasets",
            query={"filter": json.dumps({"where": {"pid": {"inq": pids}}}, separators=(",", ":"))},
        )
        if not isinstance(result, list):
            raise BatchError("SciCat returned an unexpected PID-status response.")
        return result

    def mark_files_ready(self, pid: str) -> None:
        encoded = urllib.parse.quote(pid, safe="")
        self.request(
            "PATCH",
            f"/Datasets/{encoded}/datasetlifecycle",
            body={"archiveStatusMessage": "datasetCreated", "archivable": True},
        )


def proposal_number(root: Path) -> str:
    matches = set(re.findall(r"(?i)P(\d{8})", root.name))
    if len(matches) != 1:
        raise BatchError(f"Expected exactly one P-number in proposal directory name: {root.name}")
    return next(iter(matches))


def proposal_pid(value: str, prefix: str) -> str:
    value = value.strip()
    if value.startswith(prefix):
        number = value[len(prefix) :]
    else:
        number = value[1:] if value.upper().startswith("P") else value
    if not re.fullmatch(r"\d{8}", number):
        raise BatchError(f"Invalid proposal ID: {value}")
    return prefix + number


def locate_layout(root: Path) -> dict[str, Path]:
    root = root.resolve()
    if not root.is_dir():
        raise BatchError(f"Proposal root does not exist: {root}")
    candidates: list[dict[str, Path]] = []
    for name in ("experiment", "experiments"):
        experiment = root / "data" / name
        raw = experiment / "tpx3Files"
        metadata = experiment / "metadata"
        if experiment.is_dir() and raw.is_dir() and metadata.is_dir():
            candidates.append({"root": root, "experiment": experiment, "raw": raw, "metadata": metadata})
    if len(candidates) != 1:
        raise BatchError(
            f"Expected one standard data/experiment[s] layout with tpx3Files and metadata below {root}; "
            f"found {len(candidates)}."
        )
    return candidates[0]


def timestamp_range(paths: Iterable[Path]) -> tuple[str, str, list[str]]:
    starts: list[dt.datetime] = []
    ends: list[dt.datetime] = []
    sources: list[str] = []
    for path in sorted(paths):
        obj = read_json(path)
        runs = obj.get("runs", {}) if isinstance(obj, dict) else {}
        used = False
        for run in runs.values() if isinstance(runs, dict) else []:
            if not isinstance(run, dict):
                continue
            if run.get("started_at"):
                starts.append(parse_time(str(run["started_at"])))
                used = True
            if run.get("ended_at"):
                ends.append(parse_time(str(run["ended_at"])))
                used = True
        if used:
            sources.append(path.parent.name)
    if not starts or not ends:
        raise BatchError("Experiment metadata did not contain both run start and end timestamps.")
    return format_utc(min(starts)), format_utc(max(ends)), sources


def split_archive_times(
    metadata_path: Path, stem: str, first_run: str, last_run: str
) -> tuple[str, str, list[str]]:
    obj = read_json(metadata_path)
    runs = obj.get("runs", {}) if isinstance(obj, dict) else {}
    if not isinstance(runs, dict):
        raise BatchError(f"Experiment metadata has no run mapping: {metadata_path}")
    first = int(first_run)
    last = int(last_run)
    if first > last:
        raise BatchError(f"Split archive has a reversed run range: {stem}")
    width = max(len(first_run), len(last_run))
    base = metadata_path.parent.name
    names = [f"{base}_{index:0{width}d}" for index in range(first, last + 1)]
    missing = [name for name in names if name not in runs]
    if missing:
        raise BatchError(
            f"Split archive {stem}.tar.gz references runs absent from {metadata_path}: "
            + ", ".join(missing)
        )
    starts: list[dt.datetime] = []
    ends: list[dt.datetime] = []
    for name in names:
        run = runs[name]
        if not isinstance(run, dict) or not run.get("started_at") or not run.get("ended_at"):
            raise BatchError(f"Run {name} lacks complete timestamps in {metadata_path}")
        starts.append(parse_time(str(run["started_at"])))
        ends.append(parse_time(str(run["ended_at"])))
    return format_utc(min(starts)), format_utc(max(ends)), names


def grouped_member_names(archive: Path, stem: str) -> list[str]:
    names: set[str] = set()
    try:
        with tarfile.open(archive, mode="r|gz") as handle:
            for member in handle:
                parts = [part for part in Path(member.name).parts if part not in ("", ".")]
                if len(parts) >= 2 and parts[0] == stem:
                    names.add(parts[1])
    except (OSError, tarfile.TarError) as exc:
        raise BatchError(f"Could not inspect grouped archive {archive}: {exc}") from exc
    return sorted(names)


def archived_tpx3_end_time(archive: Path) -> dt.datetime:
    latest: int | float | None = None
    try:
        with tarfile.open(archive, mode="r|gz") as handle:
            for member in handle:
                if member.isfile() and member.name.lower().endswith(".tpx3"):
                    latest = member.mtime if latest is None else max(latest, member.mtime)
    except (OSError, tarfile.TarError) as exc:
        raise BatchError(f"Could not inspect incomplete acquisition archive {archive}: {exc}") from exc
    if latest is None:
        raise BatchError(f"Incomplete acquisition archive contains no .tpx3 files: {archive}")
    return dt.datetime.fromtimestamp(latest, tz=dt.timezone.utc)


def incomplete_archive_times(
    metadata_path: Path, archive: Path
) -> tuple[str, str, list[str], str] | None:
    obj = read_json(metadata_path)
    runs = obj.get("runs", {}) if isinstance(obj, dict) else {}
    if not isinstance(runs, dict):
        return None
    starts = [
        parse_time(str(run["started_at"]))
        for run in runs.values()
        if isinstance(run, dict) and run.get("started_at")
    ]
    ends = [
        parse_time(str(run["ended_at"]))
        for run in runs.values()
        if isinstance(run, dict) and run.get("ended_at")
    ]
    if not starts or ends:
        return None
    start = min(starts)
    end = archived_tpx3_end_time(archive)
    if end < start:
        raise BatchError(
            f"Latest archived .tpx3 mtime precedes acquisition start in {metadata_path}: "
            f"{format_utc(end)} < {format_utc(start)}"
        )
    warning = (
        f"{archive.name} has started_at but no ended_at; endTime {format_utc(end)} "
        "was derived from the latest archived .tpx3 member mtime."
    )
    return format_utc(start), format_utc(end), [metadata_path.parent.name], warning


def archive_times(metadata_dir: Path, archive: Path) -> tuple[str, str, list[str], str | None]:
    stem = archive.name[: -len(".tar.gz")]
    exact = metadata_dir / stem / "experiment.json"
    if exact.is_file():
        fallback = incomplete_archive_times(exact, archive)
        if fallback:
            return fallback
        start, end, sources = timestamp_range([exact])
        return start, end, sources, None
    split = re.fullmatch(r"(.+)_part\d+_(\d+)-(\d+)", stem)
    if split:
        base, first_run, last_run = split.groups()
        metadata_path = metadata_dir / base / "experiment.json"
        if metadata_path.is_file():
            start, end, sources = split_archive_times(metadata_path, stem, first_run, last_run)
            return start, end, sources, None
    tokens = [token.lower() for token in stem.split("_") if token]
    if tokens and all(token in {"focus", "test"} for token in tokens):
        names = grouped_member_names(archive, stem)
        paths = [metadata_dir / name / "experiment.json" for name in names]
        missing = [path for path in paths if not path.is_file()]
        if missing:
            raise BatchError(
                f"Grouped archive {archive.name} has members without experiment.json: "
                + ", ".join(path.parent.name for path in missing)
            )
        if paths:
            start, end, sources = timestamp_range(paths)
            return start, end, sources, None
    raise BatchError(f"No unambiguous timestamp mapping exists for {archive.name}.")


def creation_location(proposal: dict[str, Any], override: str | None) -> str:
    if override:
        return override
    periods = proposal.get("MeasurementPeriodList", [])
    locations = {
        str(period.get("instrument", "")).strip()
        for period in periods
        if isinstance(period, dict) and str(period.get("instrument", "")).strip()
    }
    if len(locations) != 1:
        raise BatchError(
            "SciCat proposal does not identify exactly one instrument location; pass --creation-location explicitly."
        )
    return next(iter(locations))


def lifecycle(dataset: dict[str, Any]) -> dict[str, Any]:
    value = dataset.get("datasetlifecycle", {})
    return value if isinstance(value, dict) else {}


def archive_state(dataset: dict[str, Any]) -> str:
    return str(lifecycle(dataset).get("archiveStatusMessage", "")).strip().lower()


def is_on_tape(dataset: dict[str, Any]) -> bool:
    life = lifecycle(dataset)
    return archive_state(dataset) in TAPE_STATES and life.get("retrievable") is True


def archive_submitted(dataset: dict[str, Any]) -> bool:
    return archive_state(dataset) in ARCHIVE_SUBMITTED_STATES


def validate_remote(remote: dict[str, Any], entry: dict[str, Any]) -> None:
    fields = ("proposalId", "ownerGroup", "datasetName", "sourceFolder")
    mismatches = [
        f"{field}: SciCat={remote.get(field)!r}, plan={entry.get(field)!r}"
        for field in fields
        if remote.get(field) != entry.get(field)
    ]
    expected_size = entry.get("size")
    if remote.get("size") is not None and expected_size is not None and int(remote["size"]) != int(expected_size):
        mismatches.append(f"size: SciCat={remote['size']}, plan={expected_size}")
    if mismatches:
        raise BatchError("Existing SciCat dataset differs from the plan:\n  " + "\n  ".join(mismatches))


def raw_entries(
    layout: dict[str, Path],
    control_root: Path,
    number: str,
    proposal_id: str,
    owner_group: str,
    location: str,
    min_age_minutes: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    archives = sorted(layout["raw"].glob("*.tar.gz"))
    if not archives:
        raise BatchError(f"No .tar.gz files found in {layout['raw']}")
    now_ns = time.time_ns()
    minimum_age_ns = int(min_age_minutes * 60 * 1_000_000_000)
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for archive in archives:
        file_stat = archive.stat()
        if now_ns - file_stat.st_mtime_ns < minimum_age_ns:
            raise BatchError(f"Archive may still be changing (younger than {min_age_minutes:g} minutes): {archive}")
        if file_stat.st_size > MAX_DATASET_BYTES:
            raise BatchError(f"Archive exceeds SciCat's 50 TB dataset maximum: {archive}")
        if file_stat.st_size < 10**9:
            warnings.append(f"{archive.name} is below the recommended 1 GB dataset size.")
        if file_stat.st_size > RECOMMENDED_MAX_BYTES:
            warnings.append(f"{archive.name} is above the recommended 1 TB dataset size.")
        stem = archive.name[: -len(".tar.gz")]
        start, end, sources, timestamp_warning = archive_times(layout["metadata"], archive)
        if timestamp_warning:
            warnings.append(timestamp_warning)
        dataset_dir = control_root / "raw" / stem
        metadata_path = dataset_dir / "metadata.json"
        listing_path = dataset_dir / "filelisting.txt"
        subject = f"{stem} ({len(sources)} grouped acquisitions)" if len(sources) > 1 else stem
        metadata = {
            "creationLocation": location,
            "sourceFolder": str(layout["raw"]),
            "datasetName": f"P{number}_{stem}",
            "creationTime": start,
            "endTime": end,
            "type": "raw",
            "ownerGroup": owner_group,
            "proposalId": proposal_id,
            "dataFormat": "tar.gz archive containing raw Timepix3/LumaCam acquisition data",
            "description": f"Raw LumaCam/Timepix3 acquisition data from {subject} under proposal P{number}.",
        }
        write_control_json(metadata_path, metadata)
        write_control_text(listing_path, archive.name + "\n")
        entry = {
            "kind": "raw",
            "datasetName": metadata["datasetName"],
            "proposalId": proposal_id,
            "ownerGroup": owner_group,
            "sourceFolder": str(layout["raw"]),
            "metadata": str(metadata_path),
            "filelisting": str(listing_path),
            "sourceFile": str(archive),
            "size": file_stat.st_size,
            "mtimeNs": file_stat.st_mtime_ns,
            "timestampSources": sources,
        }
        if timestamp_warning:
            entry["timestampWarning"] = timestamp_warning
        entries.append(entry)
    return entries, warnings


def expected_raw_names(plan: dict[str, Any]) -> list[str]:
    return [entry["datasetName"] for entry in plan.get("rawEntries", [])]


def resolve_raw_remotes(plan: dict[str, Any], datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for dataset in datasets:
        by_name.setdefault(str(dataset.get("datasetName", "")), []).append(dataset)
    resolved: list[dict[str, Any]] = []
    for entry in plan.get("rawEntries", []):
        matches = by_name.get(entry["datasetName"], [])
        if len(matches) != 1:
            raise BatchError(
                f"Expected one SciCat raw dataset named {entry['datasetName']}; found {len(matches)}."
            )
        validate_remote(matches[0], entry)
        resolved.append(matches[0])
    return resolved


def excluded_remaining(relative: Path, experiment_name: str, file_size: int | None = None) -> bool:
    parts = relative.parts
    if len(parts) >= 3 and parts[:3] == ("data", experiment_name, "tpx3Files"):
        return True
    if any(part.lower() in TRANSIENT_DIRS for part in parts[:-1]):
        return True
    name = relative.name
    lower = name.lower()
    if lower == ".gitkeep" and file_size == 0:
        return True
    if lower.startswith(".nfs") or lower.endswith(TRANSIENT_SUFFIXES):
        return True
    return False


def inventory_remaining(root: Path, experiment_name: str) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for name in sorted(directory_names):
            path = current_path / name
            relative = path.relative_to(root)
            if path.is_symlink():
                raise BatchError(f"Remaining-content inventory refuses symbolic-link directory: {path}")
            if not excluded_remaining(relative, experiment_name):
                kept_dirs.append(name)
        directory_names[:] = kept_dirs
        for name in sorted(file_names):
            path = current_path / name
            relative = path.relative_to(root)
            if path.is_symlink():
                raise BatchError(f"Remaining-content inventory refuses symbolic-link file: {path}")
            file_stat = path.stat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise BatchError(f"Remaining-content inventory found a non-regular file: {path}")
            if excluded_remaining(relative, experiment_name, file_stat.st_size):
                continue
            inventory.append(
                {"path": relative.as_posix(), "size": file_stat.st_size, "mtimeNs": file_stat.st_mtime_ns}
            )
    inventory.sort(key=lambda item: item["path"])
    if not inventory:
        raise BatchError("No non-raw proposal content remains after applying exclusions.")
    if len(inventory) > MAX_DATASET_FILES:
        raise BatchError(f"Remaining dataset has {len(inventory)} files, above SciCat's 400,000-file limit.")
    total = sum(int(item["size"]) for item in inventory)
    if total > MAX_DATASET_BYTES:
        raise BatchError("Remaining dataset exceeds SciCat's 50 TB limit.")
    return inventory


def next_remaining_name(number: str, datasets: list[dict[str, Any]]) -> str:
    prefix = f"P{number}_remaining_processed_and_supporting_content_v"
    versions: list[int] = []
    for dataset in datasets:
        name = str(dataset.get("datasetName", ""))
        match = re.fullmatch(re.escape(prefix) + r"(\d{3})", name)
        if match:
            versions.append(int(match.group(1)))
    return prefix + f"{max(versions, default=0) + 1:03d}"


def materialize_remaining(
    plan: dict[str, Any],
    plan_path: Path,
    raw_datasets: list[dict[str, Any]],
    all_datasets: list[dict[str, Any]],
    *,
    persist_plan: bool = True,
) -> dict[str, Any]:
    if not raw_datasets or not all(is_on_tape(dataset) for dataset in raw_datasets):
        raise BatchError("Every planned raw dataset must be confirmed on tape before preparing remaining content.")
    root = Path(plan["proposalRoot"])
    inventory = inventory_remaining(root, plan["experimentDirectoryName"])
    control_dir = plan_path.parent / f"{plan_path.stem}_remaining"
    inventory_path = control_dir / "inventory.json"
    listing_path = control_dir / "filelisting.txt"
    metadata_path = control_dir / "metadata.json"
    write_control_json(inventory_path, inventory)
    write_control_text(listing_path, "".join(item["path"] + "\n" for item in inventory))
    total = sum(int(item["size"]) for item in inventory)
    first = min(int(item["mtimeNs"]) for item in inventory) / 1_000_000_000
    last = max(int(item["mtimeNs"]) for item in inventory) / 1_000_000_000
    raw_pids = sorted(str(dataset["pid"]) for dataset in raw_datasets)
    name = next_remaining_name(plan["proposalNumber"][1:], all_datasets)
    metadata = {
        "creationLocation": plan["creationLocation"],
        "sourceFolder": str(root),
        "datasetName": name,
        "creationTime": format_utc(dt.datetime.fromtimestamp(first, tz=dt.timezone.utc)),
        "endTime": format_utc(dt.datetime.fromtimestamp(last, tz=dt.timezone.utc)),
        "type": "derived",
        "ownerGroup": plan["ownerGroup"],
        "proposalId": plan["proposalId"],
        "inputDatasets": raw_pids,
        "dataFormat": "mixed LumaCam proposal files excluding raw tpx3Files archives and transient files",
        "description": (
            f"Processed results, metadata, logs, documentation, and supporting content for "
            f"{plan['proposalNumber']}; derived from all linked raw LumaCam/Timepix3 datasets."
        ),
    }
    write_control_json(metadata_path, metadata)
    entry = {
        "kind": "remaining",
        "datasetName": name,
        "proposalId": plan["proposalId"],
        "ownerGroup": plan["ownerGroup"],
        "sourceFolder": str(root),
        "metadata": str(metadata_path),
        "filelisting": str(listing_path),
        "inventory": str(inventory_path),
        "inventorySha256": file_sha256(inventory_path),
        "size": total,
        "numberOfFiles": len(inventory),
        "inputDatasets": raw_pids,
    }
    plan["remainingEntry"] = entry
    plan["remainingPreparedAt"] = format_utc(utc_now())
    if persist_plan:
        atomic_json(plan_path, plan)
    return entry


def build_plan(args: argparse.Namespace) -> int:
    root = Path(args.proposal_root).resolve()
    output = Path(args.output).resolve()
    if root == output.parent or root in output.parents:
        raise BatchError("The plan/control directory must be outside the proposal root.")
    if output.exists():
        raise BatchError(f"Refusing to overwrite an existing plan; resume it or choose a new --output path: {output}")
    layout = locate_layout(root)
    if args.raw_source_dir:
        raw_argument = Path(args.raw_source_dir).expanduser()
        if raw_argument.is_symlink():
            raise BatchError(f"External raw-source directory must not be a symbolic link: {raw_argument}")
        raw_source = raw_argument.resolve()
        if not raw_source.is_dir():
            raise BatchError(f"External raw-source directory does not exist: {raw_source}")
        if raw_source == output.parent or raw_source in output.parents:
            raise BatchError("The plan/control directory must be outside the external raw-source directory.")
        layout = {**layout, "raw": raw_source}
    number = proposal_number(root)
    prefix = args.pid_prefix
    proposal_id = proposal_pid(args.proposal_id or number, prefix)
    tokens = TokenProvider()
    client = SciCatClient(tokens, args.api_url)
    proposal = client.proposal(proposal_id)
    owner_group = str(proposal.get("ownerGroup", ""))
    if not re.fullmatch(r"p\d+", owner_group):
        raise BatchError(f"Proposal ownerGroup is missing or invalid: {owner_group!r}")
    location = creation_location(proposal, args.creation_location)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw: list[dict[str, Any]] = []
    warnings: list[str] = []
    if args.mode in {"all", "raw", "remaining"}:
        # Remaining mode also audits local raw archives so every raw PID can be linked.
        raw, warnings = raw_entries(
            layout,
            output.parent / f"{output.stem}_control",
            number,
            proposal_id,
            owner_group,
            location,
            args.min_age_minutes,
        )
    plan: dict[str, Any] = {
        "schemaVersion": 2,
        "createdAt": format_utc(utc_now()),
        "mode": args.mode,
        "proposalRoot": str(root),
        "proposalNumber": f"P{number}",
        "proposalId": proposal_id,
        "ownerGroup": owner_group,
        "creationLocation": location,
        "experimentDirectoryName": layout["experiment"].name,
        "rawDirectory": str(layout["raw"]),
        "metadataDirectory": str(layout["metadata"]),
        "apiUrl": args.api_url,
        "archiveHost": args.archive_host,
        "pidPrefix": prefix,
        "rawEntries": raw,
        "remainingPolicy": {
            "oneDataset": True,
            "excludeRawTpx3": True,
            "excludeTransient": sorted(TRANSIENT_DIRS),
            "versionIfNewFilesAppear": True,
            "requireAllRawOnTape": True,
        },
        "warnings": warnings,
    }
    if args.mode == "remaining":
        datasets = client.proposal_datasets(proposal_id)
        raw_remote = resolve_raw_remotes(plan, datasets)
        materialize_remaining(plan, output, raw_remote, datasets, persist_plan=False)
    atomic_json(output, plan)
    total = sum(int(entry["size"]) for entry in raw)
    print(f"Prepared {args.mode!r} plan for {plan['proposalNumber']} ({len(raw)} raw archives, {total/10**12:.3f} TB).")
    print(f"Plan: {output}")
    for warning in warnings:
        print("WARNING:", warning)
    print("Next: run validate with a separate --output report path; validation does not modify SciCat.")
    return 0


def load_plan(path: Path) -> dict[str, Any]:
    plan = read_json(path)
    if not isinstance(plan, dict) or plan.get("schemaVersion") != 2:
        raise BatchError(f"Unsupported or invalid plan: {path}")
    return plan


def check_entry(entry: dict[str, Any]) -> None:
    if entry["kind"] == "raw":
        source = Path(entry["sourceFile"])
        if not source.is_file():
            raise BatchError(f"Planned archive is missing: {source}")
        current = source.stat()
        if current.st_size != int(entry["size"]) or current.st_mtime_ns != int(entry["mtimeNs"]):
            raise BatchError(f"Raw archive changed since prepare; create a new plan: {source}")
    else:
        inventory_path = Path(entry["inventory"])
        if file_sha256(inventory_path) != entry["inventorySha256"]:
            raise BatchError("Remaining inventory control file changed; prepare/validate a new version.")
        inventory = read_json(inventory_path)
        for item in inventory:
            source = Path(entry["sourceFolder"]) / item["path"]
            if not source.is_file():
                raise BatchError(f"Remaining-content file disappeared: {source}")
            current = source.stat()
            if current.st_size != int(item["size"]) or current.st_mtime_ns != int(item["mtimeNs"]):
                raise BatchError(f"Remaining-content file changed; prepare a new version: {source}")
    for key in ("metadata", "filelisting"):
        if not Path(entry[key]).is_file():
            raise BatchError(f"Missing generated control file: {entry[key]}")


def state_file(plan_path: Path) -> Path:
    return plan_path.with_name(plan_path.stem + "_state.json")


def logs_root(plan_path: Path) -> Path:
    return plan_path.parent / f"{plan_path.stem}_logs"


def load_state(plan_path: Path) -> dict[str, Any]:
    path = state_file(plan_path)
    if not path.exists():
        return {"schemaVersion": 2, "datasets": {}, "validation": {}}
    state = read_json(path)
    if not isinstance(state, dict) or not isinstance(state.get("datasets"), dict):
        raise BatchError(f"Invalid state file: {path}")
    state.setdefault("validation", {})
    return state


def save_state(plan_path: Path, state: dict[str, Any]) -> None:
    atomic_json(state_file(plan_path), state)


def record_dataset(plan_path: Path, state: dict[str, Any], name: str, **values: Any) -> None:
    item = state["datasets"].setdefault(name, {})
    item.update(values)
    item["updatedAt"] = format_utc(utc_now())
    save_state(plan_path, state)


@contextlib.contextmanager
def workflow_lock(plan_path: Path) -> Iterator[None]:
    lock_path = plan_path.with_name("." + plan_path.stem + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise BatchError(f"Another process is already using {plan_path.parent}") from exc
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def rsync_progress_summary(line: str) -> str | None:
    """Return a compact status for an rsync progress record."""
    match = RSYNC_PROGRESS_RE.search(line)
    if not match:
        return None
    return f"{match.group('percent')}% | {match.group('rate')} | ETA {match.group('eta')}"


def write_live_status(message: str, *, finish: bool = False) -> None:
    """Replace one interactive terminal line; never emit progress-line scrollback."""
    width = max(20, shutil.get_terminal_size((120, 24)).columns)
    if len(message) >= width:
        message = "…" + message[-(width - 2) :]
    end = "\n" if finish else ""
    print(f"\r\033[2K{message}", end=end, flush=True)


def elapsed_summary(seconds: float) -> str:
    elapsed = max(0, int(seconds))
    hours, remainder = divmod(elapsed, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def run_logged(command: list[str], log_path: Path, label: str) -> tuple[int, str]:
    """Log full output while showing rsync progress on one changing terminal line."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    next_update = started + 60
    print(f"Starting {label}; detailed output: {log_path}")
    start_offset = log_path.stat().st_size if log_path.exists() else 0
    interactive = sys.stdout.isatty()
    live_status = False
    pending = bytearray()
    with log_path.open("ab") as log:
        log.write(f"\n[{format_utc(utc_now())}] START {label}\n".encode("utf-8"))
        log.flush()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if process.stdout is None:
            raise BatchError(f"Could not capture output for {label}.")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while selector.get_map():
                events = selector.select(timeout=1.0)
                for key, _ in events:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    log.write(chunk)
                    log.flush()
                    pending.extend(chunk)
                    while True:
                        separators = [
                            index for index in (pending.find(b"\r"), pending.find(b"\n")) if index >= 0
                        ]
                        if not separators:
                            break
                        boundary = min(separators)
                        record = bytes(pending[:boundary]).decode("utf-8", errors="replace")
                        del pending[: boundary + 1]
                        progress = rsync_progress_summary(record)
                        if progress and interactive:
                            write_live_status(f"{label}: {progress}")
                            live_status = True
                now = time.monotonic()
                if process.poll() is None and now >= next_update:
                    elapsed = elapsed_summary(now - started)
                    message = f"{label} still running ({elapsed}); details: {log_path}"
                    if interactive:
                        write_live_status(message)
                        live_status = True
                    else:
                        print(message)
                    next_update = now + 60
        finally:
            selector.close()
        code = int(process.wait())
        process.stdout.close()
        if pending:
            progress = rsync_progress_summary(pending.decode("utf-8", errors="replace"))
            if progress and interactive:
                write_live_status(f"{label}: {progress}")
                live_status = True
        log.write(f"\n[{format_utc(utc_now())}] EXIT {code}\n".encode("utf-8"))
        log.flush()
    if live_status:
        outcome = "finished" if code == 0 else f"failed (exit {code})"
        write_live_status(f"{label}: {outcome} in {elapsed_summary(time.monotonic() - started)}", finish=True)
    with log_path.open("rb") as log:
        log.seek(start_offset)
        output = log.read().decode("utf-8", errors="replace")
    return code, output


def scicat_command(token: str, entry: dict[str, Any], executable: str, ingest: bool) -> list[str]:
    command = [
        executable,
        "datasetIngestor",
        "--token",
        token,
        "--copy",
        "--allowexistingsource",
    ]
    if ingest:
        command.append("--ingest")
    command.extend([entry["metadata"], entry["filelisting"]])
    return command


def remote_by_name(datasets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for dataset in datasets:
        result.setdefault(str(dataset.get("datasetName", "")), []).append(dataset)
    return result


def validate_command(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    output = Path(args.output).resolve()
    plan = load_plan(plan_path)
    tokens = TokenProvider()
    client = SciCatClient(tokens, plan["apiUrl"])
    executable = shutil.which(args.scicat_cli)
    if not executable:
        raise BatchError(f"SciCat CLI was not found: {args.scicat_cli}")
    datasets = client.proposal_datasets(plan["proposalId"])
    by_name = remote_by_name(datasets)
    state = load_state(plan_path)
    phases: dict[str, Any] = {}

    if plan["mode"] in {"all", "raw"}:
        checked: list[dict[str, Any]] = []
        for entry in plan["rawEntries"]:
            check_entry(entry)
            matches = by_name.get(entry["datasetName"], [])
            if len(matches) > 1:
                raise BatchError(f"Multiple SciCat datasets are named {entry['datasetName']}.")
            if matches:
                validate_remote(matches[0], entry)
                checked.append({"datasetName": entry["datasetName"], "result": "existing", "pid": matches[0]["pid"]})
                continue
            log = logs_root(plan_path) / entry["datasetName"] / "dry_run.log"
            code, text = run_logged(
                scicat_command(tokens.get(), entry, executable, False), log, f"dry run {entry['datasetName']}"
            )
            if code != 0 or "dry' mode" not in text:
                raise BatchError(f"Dry run failed for {entry['datasetName']}; see {log}")
            checked.append({"datasetName": entry["datasetName"], "result": "dry_run_ok"})
        phases["raw"] = {"ok": True, "datasets": checked}

    if plan["mode"] == "all" and not plan.get("remainingEntry"):
        try:
            raw_remote = resolve_raw_remotes(plan, datasets)
        except BatchError:
            raw_remote = []
        if raw_remote and all(is_on_tape(dataset) for dataset in raw_remote):
            entry = materialize_remaining(plan, plan_path, raw_remote, datasets)
            datasets = client.proposal_datasets(plan["proposalId"])
            by_name = remote_by_name(datasets)
            phases["remainingPrepared"] = {
                "datasetName": entry["datasetName"],
                "numberOfFiles": entry["numberOfFiles"],
                "size": entry["size"],
            }
        else:
            phases["remaining"] = {"ok": False, "deferred": "raw datasets are not all confirmed on tape"}

    remaining = plan.get("remainingEntry")
    if plan["mode"] in {"all", "remaining"} and remaining:
        check_entry(remaining)
        matches = by_name.get(remaining["datasetName"], [])
        if len(matches) > 1:
            raise BatchError(f"Multiple SciCat datasets are named {remaining['datasetName']}.")
        if matches:
            validate_remote(matches[0], remaining)
            result = {"result": "existing", "pid": matches[0]["pid"]}
        else:
            log = logs_root(plan_path) / remaining["datasetName"] / "dry_run.log"
            code, text = run_logged(
                scicat_command(tokens.get(), remaining, executable, False),
                log,
                f"dry run {remaining['datasetName']}",
            )
            if code != 0 or "dry' mode" not in text:
                raise BatchError(f"Dry run failed for {remaining['datasetName']}; see {log}")
            result = {"result": "dry_run_ok"}
        phases["remaining"] = {"ok": True, "dataset": result}

    plan_hash = file_sha256(plan_path)
    validated = {
        "ok": True,
        "validatedAt": format_utc(utc_now()),
        "plan": str(plan_path),
        "planSha256": plan_hash,
        "phases": phases,
    }
    atomic_json(output, validated)
    state["validation"] = {"planSha256": plan_hash, "phases": phases, "report": str(output)}
    save_state(plan_path, state)
    print(f"Validation passed; report: {output}")
    if phases.get("remaining", {}).get("deferred"):
        print("Raw dry runs passed. Remaining content stays deferred until every raw dataset is confirmed on tape.")
    else:
        print("The prepared phase(s) may now be executed with explicit confirmation.")
    return 0


def ensure_kerberos(principal: str) -> None:
    if subprocess.run(["klist", "-s"], check=False).returncode == 0:
        return
    print(f"Kerberos ticket missing or expired; running kinit for {principal}.")
    if subprocess.run(["kinit", principal], check=False).returncode != 0:
        raise BatchError("Could not obtain a Kerberos ticket.")


def ensure_known_host(host: str) -> None:
    check = subprocess.run(
        ["ssh-keygen", "-F", host], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if check.returncode != 0:
        raise BatchError(f"{host} is not in ~/.ssh/known_hosts; verify the PSI fingerprint manually first.")


def running_archive_rsync(host: str) -> list[str]:
    result = subprocess.run(
        ["pgrep", "-a", "-u", str(os.getuid()), "rsync"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return [line for line in result.stdout.splitlines() if host in line]


def username_from_token(token: str) -> str:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        username = str(json.loads(base64.urlsafe_b64decode(payload.encode("ascii"))).get("username", ""))
    except (IndexError, ValueError, json.JSONDecodeError):
        username = ""
    if not username:
        raise BatchError("The token does not expose a PSI username; pass --archive-username explicitly.")
    return username


def resume_copy(
    entry: dict[str, Any], pid: str, username: str, host: str, log_path: Path
) -> None:
    short_pid = pid.split("/", 1)[1]
    source = entry["sourceFolder"].rstrip("/")
    destination = f"{username}@{host}:archive/{short_pid}{source}"
    command = [
        "/usr/bin/rsync",
        "-r",
        "--files-from",
        entry["filelisting"],
        "-e",
        "ssh -o ServerAliveInterval=60 -o ServerAliveCountMax=10",
        "-avx",
        "--info=progress2",
        "--partial",
        "--stderr=error",
        source + "/",
        destination,
    ]
    code, _ = run_logged(command, log_path, f"resumable copy {entry['datasetName']}")
    if code != 0:
        raise BatchError(f"rsync failed with exit {code}; state is preserved and rerunning resumes the same PID.")


def submit_archive(
    token: str, pid: str, owner_group: str, executable: str, log_path: Path
) -> str | None:
    command = [
        executable,
        "datasetArchiver",
        "--token",
        token,
        "--noninteractive",
        "--ownergroup",
        owner_group,
        pid,
    ]
    code, output = run_logged(command, log_path, f"archive submission {pid}")
    lowered = output.lower()
    if code != 0 or "could not create" in lowered or "error status" in lowered:
        raise BatchError(f"Archive-job submission failed for {pid}; state is preserved for review/resume.")
    matches = JOB_ID_RE.findall(output)
    return matches[-1] if matches else None


def process_entry(
    entry: dict[str, Any],
    remote: dict[str, Any] | None,
    plan: dict[str, Any],
    plan_path: Path,
    state: dict[str, Any],
    client: SciCatClient,
    tokens: TokenProvider,
    executable: str,
    archive_username: str,
    principal: str,
) -> None:
    name = entry["datasetName"]
    log_dir = logs_root(plan_path) / name
    if remote:
        validate_remote(remote, entry)
        pid = str(remote["pid"])
        previous = state["datasets"].get(name, {})
        if previous.get("pid") == pid and previous.get("status") == "archive_submitted":
            print(f"Skipping {name}: this workflow already submitted its archive job; use monitor.")
            return
        record_dataset(plan_path, state, name, pid=pid, status="existing_dataset_found")
        if archive_submitted(remote):
            record_dataset(plan_path, state, name, pid=pid, status=archive_state(remote))
            print(f"Skipping {name}: archive state is {archive_state(remote)!r}.")
            return
    else:
        ensure_kerberos(principal)
        code, output = run_logged(
            scicat_command(tokens.get(), entry, executable, True), log_dir / "ingest.log", f"ingest {name}"
        )
        match = DATASET_PID_RE.search(output)
        if not match:
            raise BatchError(f"SciCat did not report a created PID for {name}; see {log_dir / 'ingest.log'}")
        pid = match.group(1)
        if not pid.startswith(plan["pidPrefix"]):
            raise BatchError(f"Created PID {pid!r} does not use expected prefix {plan['pidPrefix']!r}.")
        record_dataset(plan_path, state, name, pid=pid, status="dataset_created", ingestorExit=code)
        matches = [item for item in client.proposal_datasets(plan["proposalId"]) if item.get("datasetName") == name]
        if len(matches) != 1:
            raise BatchError(f"Could not resolve the newly created dataset {name} uniquely.")
        remote = matches[0]
        validate_remote(remote, entry)

    if not bool(lifecycle(remote).get("archivable", False)):
        ensure_kerberos(principal)
        resume_copy(entry, pid, archive_username, plan["archiveHost"], log_dir / "copy.log")
        client.mark_files_ready(pid)
        record_dataset(plan_path, state, name, pid=pid, status="files_ready")
    job_id = submit_archive(tokens.get(), pid, entry["ownerGroup"], executable, log_dir / "archive.log")
    record_dataset(plan_path, state, name, pid=pid, status="archive_submitted", archiveJobId=job_id)
    print(f"Archive job submitted for {name}: {pid}")


def validation_allows(plan_path: Path, state: dict[str, Any], phase: str) -> None:
    validation = state.get("validation", {})
    if validation.get("planSha256") != file_sha256(plan_path):
        raise BatchError("The current plan has not been validated, or changed after validation. Run validate again.")
    phase_status = validation.get("phases", {}).get(phase, {})
    if phase_status.get("ok") is not True:
        raise BatchError(f"The {phase} phase has not passed validation.")


def execute_command(args: argparse.Namespace) -> int:
    command_started = time.monotonic()
    plan_path = Path(args.plan).resolve()
    output = Path(args.output).resolve()
    plan = load_plan(plan_path)
    state = load_state(plan_path)
    tokens = TokenProvider()
    client = SciCatClient(tokens, plan["apiUrl"])
    executable = shutil.which(args.scicat_cli)
    if not executable:
        raise BatchError(f"SciCat CLI was not found: {args.scicat_cli}")
    datasets = client.proposal_datasets(plan["proposalId"])
    by_name = remote_by_name(datasets)

    phase: str
    entries: list[dict[str, Any]]
    if plan["mode"] in {"all", "raw"}:
        raw_remote = []
        raw_complete = True
        for entry in plan["rawEntries"]:
            matches = by_name.get(entry["datasetName"], [])
            if len(matches) == 1:
                raw_remote.append(matches[0])
                if not archive_submitted(matches[0]):
                    raw_complete = False
            else:
                raw_complete = False
        if not raw_complete:
            phase, entries = "raw", plan["rawEntries"]
        elif plan["mode"] == "raw":
            atomic_json(output, {"ok": True, "result": "raw archive jobs already submitted", "at": format_utc(utc_now())})
            print("All raw archive jobs were already submitted. Run monitor to confirm tape completion.")
            return 0
        elif not all(is_on_tape(item) for item in raw_remote):
            atomic_json(output, {"ok": True, "result": "waiting for raw tape confirmation", "at": format_utc(utc_now())})
            print("Raw jobs are submitted but not all datasets are confirmed on tape. Run monitor next.")
            return 0
        elif plan.get("remainingEntry"):
            phase, entries = "remaining", [plan["remainingEntry"]]
        else:
            raise BatchError("Raw data are on tape. Run validate again to prepare and dry-run remaining content.")
    else:
        if not plan.get("remainingEntry"):
            raise BatchError("Remaining-only plan has no remaining dataset entry.")
        phase, entries = "remaining", [plan["remainingEntry"]]

    validation_allows(plan_path, state, phase)
    for entry in entries:
        check_entry(entry)
    ensure_known_host(plan["archiveHost"])
    running = running_archive_rsync(plan["archiveHost"])
    if running:
        raise BatchError("Another rsync to the archive host is running; wait before executing:\n  " + "\n  ".join(running))
    confirmation = f"{plan['proposalNumber']} {phase.upper()}"
    answer = input(
        f"Production {phase} phase will create/copy/archive {len(entries)} dataset(s). "
        f"Type {confirmation} to continue: "
    ).strip()
    if answer != confirmation:
        raise BatchError("Confirmation did not match; no production action was taken.")
    token = tokens.get()
    archive_username = args.archive_username or username_from_token(token)
    principal = args.kerberos_principal or f"{archive_username}@D.PSI.CH"
    report: dict[str, Any] = {"ok": False, "phase": phase, "startedAt": format_utc(utc_now()), "datasets": []}
    atomic_json(output, report)
    with workflow_lock(plan_path):
        for entry in entries:
            matches = by_name.get(entry["datasetName"], [])
            if len(matches) > 1:
                raise BatchError(f"Multiple SciCat datasets are named {entry['datasetName']}.")
            try:
                process_entry(
                    entry,
                    matches[0] if matches else None,
                    plan,
                    plan_path,
                    state,
                    client,
                    tokens,
                    executable,
                    archive_username,
                    principal,
                )
            except Exception as exc:
                record_dataset(plan_path, state, entry["datasetName"], status="error", error=str(exc))
                report["error"] = {"datasetName": entry["datasetName"], "message": str(exc)}
                atomic_json(output, report)
                raise
            report["datasets"].append(entry["datasetName"])
            atomic_json(output, report)
    elapsed_seconds = time.monotonic() - command_started
    planned_bytes = sum(int(entry.get("size", 0)) for entry in entries)
    report.update(
        {
            "ok": True,
            "finishedAt": format_utc(utc_now()),
            "elapsedSeconds": round(elapsed_seconds, 3),
            "plannedBytes": planned_bytes,
        }
    )
    atomic_json(output, report)
    print("Execution summary:")
    print(f"  Proposal: {plan['proposalNumber']} ({plan['proposalId']})")
    print(
        f"  {phase.capitalize()} datasets handled: {len(report['datasets'])}/{len(entries)} "
        f"({planned_bytes / 10**12:.3f} TB)"
    )
    print(f"  Elapsed: {elapsed_summary(elapsed_seconds)}; report: {output}")
    print("  Archive jobs submitted; physical tape confirmation is still pending.")
    print("Next: run monitor with a 600-second interval.")
    return 0


def planned_pids(plan: dict[str, Any], state: dict[str, Any]) -> list[str]:
    names = expected_raw_names(plan)
    if plan.get("remainingEntry"):
        names.append(plan["remainingEntry"]["datasetName"])
    return sorted(
        {
            str(state["datasets"].get(name, {}).get("pid"))
            for name in names
            if state["datasets"].get(name, {}).get("pid")
        }
    )


def status_rows(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset in sorted(datasets, key=lambda value: str(value.get("datasetName", ""))):
        life = lifecycle(dataset)
        rows.append(
            {
                "datasetName": dataset.get("datasetName"),
                "pid": dataset.get("pid"),
                "archiveStatusMessage": life.get("archiveStatusMessage", ""),
                "archivable": life.get("archivable"),
                "retrievable": life.get("retrievable"),
                "confirmedOnTape": is_on_tape(dataset),
            }
        )
    return rows


def fetch_planned_status(
    plan: dict[str, Any], state: dict[str, Any], client: SciCatClient
) -> tuple[list[dict[str, Any]], list[str]]:
    pids = planned_pids(plan, state)
    if not pids:
        datasets = client.proposal_datasets(plan["proposalId"])
        planned_names = set(expected_raw_names(plan))
        if plan.get("remainingEntry"):
            planned_names.add(plan["remainingEntry"]["datasetName"])
        selected = [item for item in datasets if item.get("datasetName") in planned_names]
        return selected, sorted(planned_names - {str(item.get("datasetName")) for item in selected})
    datasets = client.datasets_by_pids(pids)
    missing = sorted(set(pids) - {str(item.get("pid")) for item in datasets})
    return datasets, missing


def status_command(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    plan = load_plan(plan_path)
    state = load_state(plan_path)
    client = SciCatClient(TokenProvider(), plan["apiUrl"])
    datasets, missing = fetch_planned_status(plan, state, client)
    rows = status_rows(datasets)
    report = {"checkedAt": format_utc(utc_now()), "datasets": rows, "missing": missing}
    atomic_json(Path(args.output).resolve(), report)
    print(f"Status: {sum(row['confirmedOnTape'] for row in rows)}/{len(rows)} confirmed on tape; report: {args.output}")
    if missing:
        print(f"WARNING: {len(missing)} planned dataset/PID reference(s) were not found.")
    return 0


def raw_names_on_tape(plan: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    by_name = {row["datasetName"]: row for row in rows}
    names = expected_raw_names(plan)
    return bool(names) and all(by_name.get(name, {}).get("confirmedOnTape") is True for name in names)


def target_complete(plan: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[bool, str]:
    if plan["mode"] == "raw":
        return raw_names_on_tape(plan, rows), "raw"
    if plan["mode"] == "remaining":
        name = plan.get("remainingEntry", {}).get("datasetName")
        match = next((row for row in rows if row["datasetName"] == name), None)
        return bool(match and match["confirmedOnTape"]), "remaining"
    remaining_name = plan.get("remainingEntry", {}).get("datasetName")
    if remaining_name:
        match = next((row for row in rows if row["datasetName"] == remaining_name), None)
        return raw_names_on_tape(plan, rows) and bool(match and match["confirmedOnTape"]), "all"
    return raw_names_on_tape(plan, rows), "raw-before-remaining"


def monitor_command(args: argparse.Namespace) -> int:
    if args.interval_seconds < 600:
        raise BatchError("Monitoring interval must be at least 600 seconds for the agreed low-load policy.")
    if args.timeout_seconds != 0 and args.timeout_seconds < args.interval_seconds:
        raise BatchError("Timeout must be zero (no deadline) or at least one monitoring interval.")
    plan_path = Path(args.plan).resolve()
    output = Path(args.output).resolve()
    plan = load_plan(plan_path)
    state = load_state(plan_path)
    client = SciCatClient(TokenProvider(), plan["apiUrl"])
    started = time.monotonic()
    while True:
        datasets, missing = fetch_planned_status(plan, state, client)
        rows = status_rows(datasets)
        complete, phase = target_complete(plan, rows)
        report = {
            "checkedAt": format_utc(utc_now()),
            "phase": phase,
            "complete": complete,
            "datasets": rows,
            "missing": missing,
        }
        atomic_json(output, report)
        print(
            f"{report['checkedAt']}: {sum(row['confirmedOnTape'] for row in rows)}/{len(rows)} "
            f"confirmed on tape ({phase})."
        )
        if complete and not missing:
            state["lastTapeConfirmation"] = report
            save_state(plan_path, state)
            if phase == "raw-before-remaining":
                print("All raw datasets are physically archived. Run validate again to prepare the remaining dataset.")
            else:
                print(f"Tape confirmation complete for phase: {phase}.")
            return 0
        if args.timeout_seconds and time.monotonic() - started >= args.timeout_seconds:
            raise BatchError(f"Monitoring timed out; current status is preserved in {output}")
        time.sleep(args.interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely batch-ingest standard LumaCam proposals into PSI SciCat.")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Create a deterministic raw/all/remaining plan.")
    prepare.add_argument("--mode", required=True, choices=("all", "raw", "remaining"))
    prepare.add_argument("--proposal-root", required=True)
    prepare.add_argument(
        "--raw-source-dir",
        help="Optional directory containing raw .tar.gz files when they are stored outside the proposal tree.",
    )
    prepare.add_argument("--output", required=True, help="Output batch_plan.json path.")
    prepare.add_argument("--proposal-id")
    prepare.add_argument("--creation-location")
    prepare.add_argument("--min-age-minutes", type=float, default=15.0)
    prepare.add_argument("--api-url", default=DEFAULT_API_URL)
    prepare.add_argument("--archive-host", default=DEFAULT_ARCHIVE_HOST)
    prepare.add_argument("--pid-prefix", default=DEFAULT_PID_PREFIX)
    prepare.set_defaults(func=build_plan)

    validate = sub.add_parser("validate", help="Run local checks and SciCat dry runs only.")
    validate.add_argument("--plan", required=True)
    validate.add_argument("--output", required=True)
    validate.add_argument("--scicat-cli", default="scicat-cli")
    validate.set_defaults(func=validate_command)

    execute = sub.add_parser("execute", help="Execute the next confirmed production phase.")
    execute.add_argument("--plan", required=True)
    execute.add_argument("--output", required=True)
    execute.add_argument("--scicat-cli", default="scicat-cli")
    execute.add_argument("--archive-username")
    execute.add_argument("--kerberos-principal")
    execute.set_defaults(func=execute_command)

    status_parser = sub.add_parser("status", help="Perform one combined read-only status check.")
    status_parser.add_argument("--plan", required=True)
    status_parser.add_argument("--output", required=True)
    status_parser.set_defaults(func=status_command)

    monitor = sub.add_parser("monitor", help="Poll until physical tape confirmation.")
    monitor.add_argument("--plan", required=True)
    monitor.add_argument("--output", required=True)
    monitor.add_argument("--interval-seconds", required=True, type=int)
    monitor.add_argument("--timeout-seconds", required=True, type=int, help="Use 0 for no deadline.")
    monitor.set_defaults(func=monitor_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (BatchError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
