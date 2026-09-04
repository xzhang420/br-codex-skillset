#!/usr/bin/env python3
"""Safely manage Godzilla's LumaCam develop-to-production Git workflow."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence


DEFAULT_LIVE = Path("/home/localadmin/Programs/lumacam_measurementcontrol")
DEFAULT_DEV = Path("/home/localadmin/Programs/lumacam_measurementcontrol_dev")
DEFAULT_REMOTE = "origin"
SKIP_CONFIRMATION = "Promote LumaCam dev to production without hardware test"
ACQUISITION_MARKERS = (
    "dataAcq_single.sh",
    "dataAcq_mulit.sh",
    "dataAcq_mulit_sumImages.sh",
    "tpxAcq_continuous_2.py",
    "tpxAcq_tomography.py",
    "monitor_dataAcq_sumImages.py",
    "serval",
)


class WorkflowError(RuntimeError):
    """Raised when a workflow safety condition is not satisfied."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


def clipped(text: str, limit: int = 4000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def run_command(
    arguments: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = None
    if env:
        environment = os.environ.copy()
        environment.update(env)
    try:
        result = subprocess.run(
            list(arguments),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise WorkflowError(
            f"Could not run {arguments[0]!r} in {cwd}: {error}",
            {"command": list(arguments), "cwd": str(cwd)},
        ) from error
    if check and result.returncode != 0:
        raise WorkflowError(
            f"Command failed ({result.returncode}): {' '.join(arguments)}",
            {
                "command": list(arguments),
                "cwd": str(cwd),
                "returncode": result.returncode,
                "stdout": clipped(result.stdout),
                "stderr": clipped(result.stderr),
            },
        )
    return result


def git_result(
    repository: Path,
    *arguments: str,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        ["git", *arguments], cwd=repository, check=check, timeout=timeout
    )


def git(repository: Path, *arguments: str, timeout: int = 120) -> str:
    return git_result(repository, *arguments, timeout=timeout).stdout.strip()


def resolved_directory(path: Path, label: str) -> Path:
    path = path.expanduser().resolve(strict=False)
    if not path.is_dir():
        raise WorkflowError(f"{label} does not exist: {path}")
    return path


def repository_root(path: Path, label: str) -> Path:
    path = resolved_directory(path, label)
    root = Path(git(path, "rev-parse", "--show-toplevel")).resolve()
    if root != path:
        raise WorkflowError(f"{label} is not the Git worktree root: {path}")
    return path


def common_git_directory(repository: Path) -> Path:
    raw = Path(git(repository, "rev-parse", "--git-common-dir"))
    if not raw.is_absolute():
        raw = repository / raw
    return raw.resolve()


def validate_pair(live: Path, dev: Path, remote: str) -> tuple[Path, Path]:
    live = repository_root(live, "Production worktree")
    dev = repository_root(dev, "Development worktree")
    if live == dev:
        raise WorkflowError("Production and development worktrees must be different")
    if common_git_directory(live) != common_git_directory(dev):
        raise WorkflowError("Production and development are not worktrees of the same repository")
    live_remote = git(live, "remote", "get-url", remote)
    dev_remote = git(dev, "remote", "get-url", remote)
    if live_remote != dev_remote:
        raise WorkflowError(
            "Production and development remote URLs differ",
            {"production_remote": live_remote, "development_remote": dev_remote},
        )
    return live, dev


def current_branch(repository: Path) -> str:
    return git(repository, "branch", "--show-current")


def revision(repository: Path, ref: str) -> str:
    try:
        return git(repository, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except WorkflowError as error:
        raise WorkflowError(f"Required Git reference is missing: {ref}", error.details) from error


def changed_paths(repository: Path) -> list[str]:
    commands = (
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    paths: set[str] = set()
    for arguments in commands:
        output = git_result(repository, *arguments).stdout
        paths.update(item for item in output.split("\0") if item)
    return sorted(paths)


def staged_paths(repository: Path) -> list[str]:
    output = git_result(repository, "diff", "--cached", "--name-only", "-z").stdout
    return sorted(item for item in output.split("\0") if item)


def unstaged_paths(repository: Path) -> list[str]:
    output = git_result(repository, "diff", "--name-only", "-z").stdout
    return sorted(item for item in output.split("\0") if item)


def require_branch(repository: Path, expected: str, label: str) -> None:
    actual = current_branch(repository)
    if actual != expected:
        raise WorkflowError(f"{label} must be on {expected!r}; found {actual or 'detached HEAD'!r}")


def require_clean(repository: Path, label: str) -> None:
    paths = changed_paths(repository)
    if paths:
        raise WorkflowError(
            f"{label} has uncommitted changes; preserving them and stopping",
            {"changed_paths": paths},
        )


def fetch(dev: Path, remote: str) -> None:
    git(dev, "fetch", "--prune", "--tags", remote, timeout=180)


def ahead_behind(repository: Path, left: str, right: str) -> tuple[int, int]:
    output = git(repository, "rev-list", "--left-right", "--count", f"{left}...{right}")
    try:
        left_only, right_only = output.split()
        return int(left_only), int(right_only)
    except (ValueError, TypeError) as error:
        raise WorkflowError(f"Could not parse Git divergence: {output!r}") from error


def require_remote_sync(live: Path, dev: Path, remote: str) -> dict[str, str]:
    refs = {
        "main": revision(live, "main"),
        "remote_main": revision(live, f"refs/remotes/{remote}/main"),
        "develop": revision(dev, "develop"),
        "remote_develop": revision(dev, f"refs/remotes/{remote}/develop"),
    }
    if refs["main"] != refs["remote_main"]:
        raise WorkflowError("Local main is not synchronized with remote main", refs)
    if refs["develop"] != refs["remote_develop"]:
        raise WorkflowError("Local develop is not synchronized with remote develop", refs)
    return refs


def active_acquisition_processes() -> list[dict[str, Any]]:
    result = run_command(["ps", "-eo", "pid=,args="], cwd=Path("/"), timeout=10)
    matches: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_text, command = parts
        if not pid_text.isdigit() or int(pid_text) == os.getpid():
            continue
        lower_command = command.lower()
        if any(marker.lower() in lower_command for marker in ACQUISITION_MARKERS):
            matches.append({"pid": int(pid_text), "command": clipped(command, 1000)})
    return matches


def test_summary(output: str) -> dict[str, Any]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    count = None
    for line in reversed(lines):
        match = re.search(r"Ran (\d+) tests?", line)
        if match:
            count = int(match.group(1))
            break
    return {"test_count": count, "tail": lines[-12:]}


def run_validation(dev: Path) -> dict[str, Any]:
    started = time.monotonic()
    unit = run_command(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=dev,
        check=False,
        timeout=300,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    combined = "\n".join(part for part in (unit.stdout, unit.stderr) if part)
    report: dict[str, Any] = {
        "unit_tests": {
            "passed": unit.returncode == 0,
            "returncode": unit.returncode,
            **test_summary(combined),
        },
        "shell_syntax": {"passed": True, "checked": []},
        "parameter_json": {"passed": True, "checked": []},
    }

    shell_output = git_result(dev, "ls-files", "-z", "*.sh").stdout
    for relative in sorted(item for item in shell_output.split("\0") if item):
        result = run_command(["bash", "-n", relative], cwd=dev, check=False, timeout=30)
        report["shell_syntax"]["checked"].append(relative)
        if result.returncode != 0:
            report["shell_syntax"]["passed"] = False
            report["shell_syntax"]["failure"] = {
                "path": relative,
                "stderr": clipped(result.stderr),
            }
            break

    json_output = git_result(dev, "ls-files", "-z", "parameterSettings*.json").stdout
    for relative in sorted(item for item in json_output.split("\0") if item):
        path = dev / relative
        report["parameter_json"]["checked"].append(relative)
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            report["parameter_json"]["passed"] = False
            report["parameter_json"]["failure"] = {
                "path": relative,
                "error": str(error),
            }
            break

    report["duration_seconds"] = round(time.monotonic() - started, 3)
    passed = all(
        report[key]["passed"]
        for key in ("unit_tests", "shell_syntax", "parameter_json")
    )
    report["passed"] = passed
    if not passed:
        raise WorkflowError("LumaCam software validation failed", {"validation": report})
    return report


def status_payload(
    live: Path, dev: Path, remote: str, *, do_fetch: bool
) -> dict[str, Any]:
    live, dev = validate_pair(live, dev, remote)
    if do_fetch:
        fetch(dev, remote)
    refs = {
        "main": revision(live, "main"),
        "remote_main": revision(live, f"refs/remotes/{remote}/main"),
        "develop": revision(dev, "develop"),
        "remote_develop": revision(dev, f"refs/remotes/{remote}/develop"),
    }
    main_only, develop_only = ahead_behind(dev, "main", "develop")
    changed = git(dev, "diff", "--name-status", "main..develop").splitlines()
    commits = git(dev, "log", "--format=%H%x09%s", "main..develop").splitlines()
    live_changes = changed_paths(live)
    dev_changes = changed_paths(dev)
    active = active_acquisition_processes()
    branches_ok = current_branch(live) == "main" and current_branch(dev) == "develop"
    remotes_ok = refs["main"] == refs["remote_main"] and refs["develop"] == refs["remote_develop"]
    if dev_changes:
        next_step = (
            "Dev has unsaved changes. Continue editing only the dev directory; "
            "when ready, say 'Save my LumaCam dev change'."
        )
    elif not live_changes and branches_ok and remotes_ok:
        next_step = "To begin work, say 'Start a LumaCam dev change: <description>'."
    else:
        next_step = "Ask Codex to resolve the reported Git state before editing or promoting."
    return {
        "status": "ok",
        "operation": "status",
        "fetched": do_fetch,
        "production": {
            "path": str(live),
            "branch": current_branch(live),
            "head": refs["main"],
            "remote_head": refs["remote_main"],
            "changed_paths": live_changes,
        },
        "development": {
            "path": str(dev),
            "branch": current_branch(dev),
            "head": refs["develop"],
            "remote_head": refs["remote_develop"],
            "changed_paths": dev_changes,
        },
        "branch_difference": {
            "main_only_commits": main_only,
            "develop_only_commits": develop_only,
            "develop_commits": commits,
            "changed_files": changed,
        },
        "active_acquisition_processes": active,
        "safe_to_start_new_change": bool(
            branches_ok and remotes_ok and not live_changes and not dev_changes
        ),
        "safe_to_promote": bool(
            branches_ok
            and remotes_ok
            and not live_changes
            and not dev_changes
            and not active
            and develop_only > 0
            and main_only == 0
        ),
        "next_step": next_step,
    }


def start_payload(args: argparse.Namespace) -> dict[str, Any]:
    live, dev = validate_pair(args.live, args.dev, args.remote)
    if not args.change.strip():
        raise WorkflowError("Change description must not be empty")
    require_branch(live, "main", "Production worktree")
    require_branch(dev, "develop", "Development worktree")
    require_clean(live, "Production worktree")
    require_clean(dev, "Development worktree")
    fetch(dev, args.remote)
    if revision(live, "main") != revision(live, f"refs/remotes/{args.remote}/main"):
        raise WorkflowError("Production main is not synchronized with remote main")
    git(dev, "pull", "--ff-only", args.remote, "develop", timeout=180)
    refs = require_remote_sync(live, dev, args.remote)
    return {
        "status": "ok",
        "operation": "start",
        "change": args.change.strip(),
        "development_path": str(dev),
        "develop_sha": refs["develop"],
        "production_sha": refs["main"],
        "next_step": (
            f"Edit only {dev}. When editing is complete, say "
            "'Save my LumaCam dev change'."
        ),
    }


def test_payload(args: argparse.Namespace) -> dict[str, Any]:
    live, dev = validate_pair(args.live, args.dev, args.remote)
    require_branch(live, "main", "Production worktree")
    require_branch(dev, "develop", "Development worktree")
    validation = run_validation(dev)
    return {
        "status": "ok",
        "operation": "test",
        "development_path": str(dev),
        "develop_sha": revision(dev, "develop"),
        "validation": validation,
        "hardware_tested": False,
        "next_step": (
            "Software validation passed. Continue editing, or say "
            "'Save my LumaCam dev change' when ready."
        ),
    }


def normalize_reviewed_paths(raw_paths: list[str]) -> list[str]:
    normalized: set[str] = set()
    for raw in raw_paths:
        candidate = PurePosixPath(raw)
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise WorkflowError(f"Reviewed path must be repository-relative: {raw!r}")
        value = candidate.as_posix()
        if value in (".", ".git") or value.startswith(".git/"):
            raise WorkflowError(f"Unsafe reviewed path: {raw!r}")
        normalized.add(value)
    return sorted(normalized)


def save_payload(args: argparse.Namespace) -> dict[str, Any]:
    live, dev = validate_pair(args.live, args.dev, args.remote)
    require_branch(live, "main", "Production worktree")
    require_branch(dev, "develop", "Development worktree")
    require_clean(live, "Production worktree")
    if not args.reviewed:
        raise WorkflowError("Refusing to save until the diff has been reviewed")
    if not args.message.strip():
        raise WorkflowError("Commit message must not be empty")
    if staged_paths(dev):
        raise WorkflowError(
            "Dev already has staged files; preserving them and stopping",
            {"staged_paths": staged_paths(dev)},
        )
    dirty = changed_paths(dev)
    if not dirty:
        raise WorkflowError("Dev has no changes to save")
    reviewed = normalize_reviewed_paths(args.path)
    if set(reviewed) != set(dirty):
        raise WorkflowError(
            "Reviewed paths must exactly match all current dev changes",
            {"reviewed_paths": reviewed, "changed_paths": dirty},
        )

    fetch(dev, args.remote)
    require_remote_sync(live, dev, args.remote)
    validation = run_validation(dev)
    dirty_after_tests = changed_paths(dev)
    if set(dirty_after_tests) != set(reviewed):
        raise WorkflowError(
            "Changed-file set moved during validation; preserving work and stopping",
            {"before": reviewed, "after": dirty_after_tests},
        )

    git(dev, "add", "--", *reviewed)
    staged = staged_paths(dev)
    if set(staged) != set(reviewed) or unstaged_paths(dev):
        raise WorkflowError(
            "Staging result differs from the reviewed change set; stopping before commit",
            {
                "reviewed_paths": reviewed,
                "staged_paths": staged,
                "unstaged_paths": unstaged_paths(dev),
            },
        )
    git(dev, "commit", "-m", args.message.strip(), timeout=180)
    commit_sha = revision(dev, "HEAD")
    try:
        git(dev, "push", args.remote, "develop", timeout=180)
    except WorkflowError as error:
        raise WorkflowError(
            "Commit succeeded locally, but pushing develop failed. Preserve the commit and inspect status before retrying.",
            {"commit_sha": commit_sha, "push_error": error.details},
        ) from error
    require_clean(dev, "Development worktree")
    return {
        "status": "ok",
        "operation": "save",
        "commit_sha": commit_sha,
        "commit_message": args.message.strip(),
        "saved_paths": reviewed,
        "validation": validation,
        "pushed_branch": "develop",
        "next_step": (
            "Develop is saved and pushed. Review the result. Promote only after acceptance; "
            "experiments continue using production main."
        ),
    }


def is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    result = git_result(
        repository, "merge-base", "--is-ancestor", ancestor, descendant, check=False
    )
    if result.returncode not in (0, 1):
        raise WorkflowError(
            "Could not determine Git ancestry",
            {"stdout": clipped(result.stdout), "stderr": clipped(result.stderr)},
        )
    return result.returncode == 0


def next_production_tag(repository: Path) -> str:
    prefix = f"production-{date.today().isoformat()}."
    tags = git(repository, "tag", "--list", f"{prefix}*").splitlines()
    suffixes = []
    for tag in tags:
        match = re.fullmatch(re.escape(prefix) + r"(\d+)", tag)
        if match:
            suffixes.append(int(match.group(1)))
    return f"{prefix}{max(suffixes, default=0) + 1}"


def promote_payload(args: argparse.Namespace) -> dict[str, Any]:
    live, dev = validate_pair(args.live, args.dev, args.remote)
    require_branch(live, "main", "Production worktree")
    require_branch(dev, "develop", "Development worktree")
    require_clean(live, "Production worktree")
    require_clean(dev, "Development worktree")
    if args.skip_hardware_test and args.confirmation != SKIP_CONFIRMATION:
        raise WorkflowError(
            "Skipping hardware testing requires exact confirmation",
            {"required_confirmation": SKIP_CONFIRMATION},
        )
    fetch(dev, args.remote)
    refs = require_remote_sync(live, dev, args.remote)
    source_sha = revision(dev, args.source_sha)
    if source_sha != refs["develop"]:
        raise WorkflowError(
            "Promotion source must equal the exact current develop SHA",
            {"requested_source": source_sha, "develop_sha": refs["develop"]},
        )
    if source_sha == refs["main"]:
        raise WorkflowError("Develop is already the production commit; nothing to promote")
    if not is_ancestor(live, refs["main"], source_sha):
        raise WorkflowError(
            "Main cannot fast-forward to develop; branches have diverged",
            {"main_sha": refs["main"], "develop_sha": source_sha},
        )
    active = active_acquisition_processes()
    if active:
        raise WorkflowError(
            "Acquisition-related processes are active; promotion stopped",
            {"active_acquisition_processes": active},
        )
    validation = run_validation(dev)
    tag = next_production_tag(dev)
    try:
        git(live, "merge", "--ff-only", source_sha, timeout=180)
        git(live, "tag", "-a", tag, source_sha, "-m", f"Production {tag}")
        git(live, "push", "--atomic", args.remote, "main", f"refs/tags/{tag}", timeout=180)
    except WorkflowError as error:
        local_tag = git_result(
            live, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}", check=False
        )
        raise WorkflowError(
            "Promotion did not finish. Local main or the local tag may be ahead; inspect exact refs before retrying.",
            {
                "source_sha": source_sha,
                "tag": tag,
                "local_main": revision(live, "main"),
                "local_tag": local_tag.stdout.strip() if local_tag.returncode == 0 else None,
                "failure": error.details,
            },
        ) from error
    return {
        "status": "ok",
        "operation": "promote",
        "production_sha": source_sha,
        "production_tag": tag,
        "hardware_test": "skipped_by_explicit_user_confirmation"
        if args.skip_hardware_test
        else "reported_passed_by_user",
        "validation": validation,
        "pushed": ["main", tag],
        "next_step": (
            f"Production now runs {source_sha} tagged {tag}. Run experiments only from {live}."
        ),
    }


def rollback_plan_payload(args: argparse.Namespace) -> dict[str, Any]:
    live, dev = validate_pair(args.live, args.dev, args.remote)
    fetch(dev, args.remote)
    tag_sha = revision(dev, f"refs/tags/{args.tag}")
    main_sha = revision(live, f"refs/remotes/{args.remote}/main")
    commits = git(
        dev, "log", "--format=%H%x09%s", f"{tag_sha}..{main_sha}"
    ).splitlines()
    changed = git(dev, "diff", "--name-status", tag_sha, main_sha).splitlines()
    return {
        "status": "ok",
        "operation": "rollback-plan",
        "requested_tag": args.tag,
        "tag_sha": tag_sha,
        "current_remote_main_sha": main_sha,
        "commits_after_tag": commits,
        "changed_files_after_tag": changed,
        "active_acquisition_processes": active_acquisition_processes(),
        "mutated_repository": False,
        "next_step": (
            "Review this plan. If recovery is needed, ask Codex to prepare reviewed revert or fix-forward commits. "
            "Never reset or force-push main."
        ),
    }


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--dev", type=Path, default=DEFAULT_DEV)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--output", type=Path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Inspect both worktrees and branch differences")
    add_common_arguments(status)
    status.add_argument("--skip-fetch", action="store_true")

    start = subparsers.add_parser("start", help="Prepare clean develop for a new change")
    add_common_arguments(start)
    start.add_argument("--change", required=True)

    test = subparsers.add_parser("test", help="Run non-hardware software validation on dev")
    add_common_arguments(test)

    save = subparsers.add_parser("save", help="Validate, commit, and push reviewed dev changes")
    add_common_arguments(save)
    save.add_argument("--message", required=True)
    save.add_argument("--path", action="append", required=True)
    save.add_argument("--reviewed", action="store_true")

    promote = subparsers.add_parser("promote", help="Fast-forward production to exact develop SHA")
    add_common_arguments(promote)
    promote.add_argument("--source-sha", required=True)
    hardware = promote.add_mutually_exclusive_group(required=True)
    hardware.add_argument("--hardware-tested", action="store_true")
    hardware.add_argument("--skip-hardware-test", action="store_true")
    promote.add_argument("--confirmation")

    rollback = subparsers.add_parser("rollback-plan", help="Create a read-only recovery plan")
    add_common_arguments(rollback)
    rollback.add_argument("--tag", required=True)
    return parser


def write_output(payload: dict[str, Any], output: Path) -> None:
    output = output.expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", delete=False
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except (OSError, TypeError):
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            payload = status_payload(args.live, args.dev, args.remote, do_fetch=not args.skip_fetch)
        elif args.command == "start":
            payload = start_payload(args)
        elif args.command == "test":
            payload = test_payload(args)
        elif args.command == "save":
            payload = save_payload(args)
        elif args.command == "promote":
            payload = promote_payload(args)
        elif args.command == "rollback-plan":
            payload = rollback_plan_payload(args)
        else:
            raise WorkflowError(f"Unknown command: {args.command}")
    except WorkflowError as error:
        payload = {
            "status": "error",
            "operation": args.command,
            "error": str(error),
            "details": error.details,
            "next_step": "Stop. Preserve current files and ask Codex to inspect this report.",
        }
        try:
            write_output(payload, args.output)
        except (OSError, TypeError) as write_error:
            print(f"ERROR: {error}; could not write report: {write_error}", file=sys.stderr)
            return 1
        print(f"ERROR: {error}. Report written to: {args.output}", file=sys.stderr)
        return 1
    try:
        write_output(payload, args.output)
    except (OSError, TypeError) as error:
        print(f"ERROR writing report {args.output}: {error}", file=sys.stderr)
        return 1
    print(f"Success! Data written to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
