#!/usr/bin/env python3
"""Read-only LumaCam proposal audit. Never deletes, moves, or edits proposal files."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

KNOWN = {'.work', 'derived', 'final', 'logs', 'metadata', 'tpx3Files',
         'rawFiles', 'photonFiles', 'eventFiles'}
SHARED = {'test_focus', 'serval', 'monitor', 'batch_focus_cache', '.trash', '.work'}
DONE = {'completed', 'processed'}
UNFINISHED = {'planned', 'in_progress', 'running', 'started', 'acquiring',
              'acquired', 'processing', 'failed', 'aborted', 'interrupted',
              'cancelled', 'canceled', 'partial', 'incomplete'}
ARCHIVES = ('.tar.gz', '.tar', '.tgz', '.zip', '.tar.zst')


def component(value):
    return isinstance(value, str) and value not in {'', '.', '..'} and '/' not in value and '\\' not in value


def no_symlinks(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'Symlinked path: {part}')


def layout_root(proposal):
    proposal = Path(os.path.abspath(Path(proposal).expanduser()))
    no_symlinks(proposal)
    candidates = [proposal / 'data' / name for name in ('experiment', 'experiments')]
    present = [p for p in candidates if p.exists() or p.is_symlink()]
    if len(present) != 1 or not present[0].is_dir():
        raise ValueError('Expected exactly one data/experiment or data/experiments below the proposal')
    no_symlinks(present[0])
    return present[0]


def archive_owner(name, names):
    if not name.endswith(ARCHIVES):
        return None
    matches = [n for n in names if any(name == n + ext for ext in ARCHIVES)
               or re.fullmatch(re.escape(n) + r'_part\d+_[^/]+\.(?:tar\.gz|tar|tgz|zip|tar\.zst)', name)]
    return matches[0] if len(matches) == 1 else None


def inventory(path, section, errors):
    """lstat every entry, including directories; do not follow symlinks."""
    entries = []
    def visit(p):
        try:
            s = p.lstat()
            kind = 'dir' if stat.S_ISDIR(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'unsafe'
            entries.append({'path': str(p), 'section': section, 'kind': kind,
                            'bytes': s.st_size if kind == 'file' else 0,
                            'allocated_bytes': s.st_blocks * 512 if kind == 'file' else 0,
                            'mtime_ns': s.st_mtime_ns, 'ctime_ns': s.st_ctime_ns,
                            'device': s.st_dev, 'inode': s.st_ino, 'links': s.st_nlink})
            if kind == 'unsafe':
                errors.append(f'Symlink or special file; manual review: {p}')
            elif kind == 'dir':
                for child in sorted(p.iterdir()):
                    visit(child)
        except OSError as exc:
            errors.append(f'Cannot fully inspect {p}: {exc}')
    visit(path)
    return entries


def stats(entries):
    files = [e for e in entries if e['kind'] == 'file']
    payload = [e for e in files if e['section'] not in {'metadata', 'logs'}
               and Path(e['path']).name != '.gitkeep' and e['bytes'] > 0]
    return {'files': len(files), 'bytes': sum(e['bytes'] for e in files),
            'allocated_bytes': sum(e['allocated_bytes'] for e in files),
            'payload_files': len(payload), 'payload_bytes': sum(e['bytes'] for e in payload),
            'latest_mtime_ns': max((e['mtime_ns'] for e in entries), default=0),
            'hardlinked_files': sum(e['links'] > 1 for e in files)}


def summarize_paths(paths, entries):
    result = []
    for p in sorted(paths):
        subset = [e for e in entries if e['path'] == str(p) or p in Path(e['path']).parents]
        result.append({'path': str(p), **stats(subset)})
    return result


def classify(status, measured, exists, archived=False):
    # Acquisition success does not imply processing success, but valuable acquired data stays visible.
    flags = []
    if status in UNFINISHED:
        flags.append('unfinished_processing' if status == 'acquired' else 'unfinished')
    elif status not in DONE:
        flags.append('unknown_status')
    if not exists:
        flags.append('no_local_paths')
    if measured['payload_bytes'] == 0:
        if archived:
            flags.append('archive_coverage_unknown')
        elif status in DONE:
            flags.append('completed_but_no_local_payload')
        else:
            flags.append('no_local_payload')
    if measured['bytes'] == 0 and exists:
        flags.append('zero_byte_tree')
    return flags


def audit(proposal, recent_seconds=600, include_inventory=False):
    root = layout_root(proposal)
    errors, warnings = [], []
    sections = []
    for p in sorted(root.iterdir()):
        if p.is_symlink():
            errors.append(f'Symlinked layout entry: {p}')
        elif p.is_dir() and p.name not in {'.trash', 'test_focus'}:
            sections.append(p)
        elif p.name == 'test_focus':
            warnings.append(f'Unexpected layout-level test_focus; inspect manually: {p}')
    # Seed identities in known data sections, then include exact matches in every custom section.
    names = set()
    for section in sections:
        if section.name not in KNOWN:
            continue
        for parent in (section, section / 'test_focus'):
            if parent.is_symlink():
                errors.append(f'Symlinked grouping directory: {parent}')
                continue
            if not parent.is_dir():
                continue
            for p in parent.iterdir():
                if p.name in SHARED or p.name.startswith('.'):
                    continue
                if p.is_dir() and not p.is_symlink():
                    names.add(p.name)
                elif p.is_symlink():
                    errors.append(f'Symlinked experiment candidate: {p}')
                elif p.name.endswith(ARCHIVES) and '_part' not in p.name:
                    names.add(next(p.name[:-len(ext)] for ext in ARCHIVES if p.name.endswith(ext)))
    experiments = []
    mapped = set()
    for name in sorted(names):
        paths, archived_paths, moves, local_errors = [], [], [], []
        locations = set()
        for section in sections:
            for parent in (section, section / 'test_focus'):
                if parent.is_symlink() or not parent.is_dir():
                    continue
                for p in sorted(parent.iterdir()):
                    if p.name != name and archive_owner(p.name, names) != name:
                        continue
                    paths.append(p)
                    mapped.add(str(p))
                    grouped = parent.name == 'test_focus'
                    locations.add('grouped' if grouped else 'ungrouped')
                    if p.name.endswith(ARCHIVES):
                        archived_paths.append(str(p))
                    if ('test' in name.lower() or 'focus' in name.lower()) and not grouped:
                        destination = section / 'test_focus' / p.name
                        conflict = destination.exists() or destination.is_symlink()
                        if conflict:
                            local_errors.append(f'Grouping destination already exists: {destination}')
                        if (section / 'test_focus').is_symlink():
                            local_errors.append(f'Symlinked grouping directory: {section / "test_focus"}')
                        moves.append({'source': str(p), 'destination': str(destination), 'conflict': conflict})
        entries = []
        for p in paths:
            section = p.relative_to(root).parts[0]
            entries.extend(inventory(p, section, local_errors))
        manifests = [p / 'experiment.json' for p in paths
                     if p.relative_to(root).parts[0] == 'metadata' and p.name == name
                     and (p / 'experiment.json').is_file()]
        manifest, manifest_error = {}, None
        if len(manifests) != 1:
            manifest_error = f'Expected one experiment.json, found {len(manifests)}'
        else:
            try:
                no_symlinks(manifests[0])
                manifest = json.loads(manifests[0].read_text())
                if not isinstance(manifest, dict):
                    raise ValueError('manifest must be an object')
                if manifest.get('experiment', name) != name:
                    raise ValueError('experiment identity differs from directory name')
                if not isinstance(manifest.get('runs'), dict) or not isinstance(manifest.get('expected_runs'), list):
                    raise ValueError('runs must be an object and expected_runs a list')
                if not all(component(k) and isinstance(v, dict) and isinstance(v.get('status', 'unknown'), str)
                           for k, v in manifest['runs'].items()):
                    raise ValueError('invalid run entry')
                if not all(component(k) for k in manifest['expected_runs']):
                    raise ValueError('invalid expected run identity')
                if len(set(manifest['expected_runs'])) != len(manifest['expected_runs']):
                    raise ValueError('duplicate expected run identities')
                if not isinstance(manifest.get('status', 'unknown'), str):
                    raise ValueError('invalid experiment status')
            except (OSError, ValueError, TypeError) as exc:
                manifest_error = str(exc)
                manifest = {}
        if manifest_error:
            manifest = {}
        run_map = manifest.get('runs', {})
        run_names = set(run_map) | set(manifest.get('expected_runs', []))
        # Native run IDs are <experiment>_<five-or-more digits>. Also honor exact manifest IDs.
        pattern = re.compile(re.escape(name) + r'_\d{5,}(?=_|\.|$)')
        for e in entries:
            match = pattern.match(Path(e['path']).name)
            if match:
                run_names.add(match.group())
        # Index once: proposals can contain thousands of runs and many files per run.
        selected_by_run = {run: [] for run in run_names}
        targets_by_run = {run: set() for run in run_names}
        for e in entries:
            p = Path(e['path'])
            relative = p.relative_to(root).parts
            indices = [i for i, part in enumerate(relative[2:], start=2) if part in run_names]
            if indices:
                run = relative[indices[0]]
                targets_by_run[run].add(root.joinpath(*relative[:indices[0] + 1]))
                selected_by_run[run].append(e)
            elif e['kind'] == 'file':
                matches = [p.name[:m.start()] for m in re.finditer(r'[_\.]', p.name)
                           if p.name[:m.start()] in run_names]
                if matches:
                    run = max(matches, key=len)
                    targets_by_run[run].add(p)
                    selected_by_run[run].append(e)
        runs = []
        for run in sorted(run_names):
            selected, targets = selected_by_run[run], targets_by_run[run]
            measured = stats(selected)
            status = run_map.get(run, {}).get('status', 'unknown')
            flags = classify(status, measured, bool(targets), bool(archived_paths))
            runs.append({'run': run, 'status': status, 'flags': flags, **measured,
                         'paths': summarize_paths(targets, selected),
                         'recent': measured['latest_mtime_ns'] > (time.time() - recent_seconds) * 1e9})
        measured = stats(entries)
        status = manifest.get('status', 'unknown')
        flags = classify(status, measured, bool(paths), bool(archived_paths))
        if any(r['status'] in UNFINISHED for r in runs) and 'unfinished' not in flags:
            flags.append('contains_unfinished_runs')
        if any(r['status'] in DONE for r in runs) and any(r['status'] not in DONE for r in runs):
            flags.append('mixed_completed_and_other_runs')
        if manifest_error:
            flags.append('manifest_unavailable')
        if len(locations) > 1:
            warnings.append(f'{name}: partly grouped; inspect exact paths before continuing')
        record = {'experiment': name, 'status': status, 'flags': flags,
                  'manifest': str(manifests[0]) if len(manifests) == 1 else None,
                  'manifest_error': manifest_error,
                  'expected_runs': len(manifest['expected_runs']) if manifest else None,
                  'run_status_counts': dict(Counter(r['status'] for r in runs)),
                  **measured, 'paths': summarize_paths(paths, entries), 'runs': runs,
                  'archives': archived_paths, 'group_moves': moves, 'errors': local_errors,
                  'recent': measured['latest_mtime_ns'] > (time.time() - recent_seconds) * 1e9}
        if include_inventory:
            record['inventory'] = entries
        experiments.append(record)
    for section in sections:
        for parent in (section, section / 'test_focus'):
            if parent.is_symlink() or not parent.is_dir():
                continue
            for p in sorted(parent.iterdir()):
                if str(p) not in mapped and p.name not in SHARED and p.name != '.gitkeep':
                    warnings.append(f'Unmapped entry (preserve; investigate if experiment-owned): {p}')
    return {'proposal': str(root.parent.parent), 'layout': str(root),
            'recent_seconds': recent_seconds, 'errors': errors, 'warnings': warnings,
            'experiments': experiments}


def human_bytes(n):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'):
        if n < 1024 or unit == 'PiB':
            return f'{n:.2f} {unit}'
        n /= 1024


def render(report):
    print(f"Proposal: {report['proposal']}\nLayout: {report['layout']}")
    print('Read-only inventory; flags are review candidates, never deletion authorization.')
    print('Sizes overlap: experiment totals contain run totals. Allocated size is not guaranteed freed space.')
    for e in report['experiments']:
        print(f"\nEXPERIMENT {e['experiment']} | status={e['status']} | {', '.join(e['flags']) or 'complete'}")
        print(f"  files={e['files']} size={human_bytes(e['bytes'])} allocated={human_bytes(e['allocated_bytes'])} "
              f"payload={human_bytes(e['payload_bytes'])} recent={e['recent']}")
        print(f"  expected={e['expected_runs']} statuses={e['run_status_counts']}")
        for p in e['paths']:
            print(f"  PATH {p['path']} | {human_bytes(p['bytes'])} allocated={human_bytes(p['allocated_bytes'])}")
        if e['manifest_error']:
            print(f"  REVIEW {e['manifest_error']}")
        for r in e['runs']:
            if r['flags']:
                print(f"  RUN {r['run']} | {r['status']} | {', '.join(r['flags'])} | "
                      f"files={r['files']} size={human_bytes(r['bytes'])} allocated={human_bytes(r['allocated_bytes'])} recent={r['recent']}")
        for m in e['group_moves']:
            print(f"  GROUP {m['source']} -> {m['destination']}" + (' [CONFLICT]' if m['conflict'] else ''))
        for error in e['errors']:
            print(f'  BLOCKED {error}')
    for text in report['errors'] + report['warnings']:
        print(f'REVIEW {text}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('proposal')
    parser.add_argument('--json', action='store_true', help='Emit full run/path report as JSON')
    parser.add_argument('--inventory', action='store_true', help='Include per-entry lstat snapshots in JSON for rechecks')
    parser.add_argument('--recent-seconds', type=int, default=600)
    args = parser.parse_args()
    if args.recent_seconds < 0:
        parser.error('--recent-seconds must be nonnegative')
    try:
        report = audit(args.proposal, args.recent_seconds, args.inventory)
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        render(report)
    return 2 if report['errors'] or any(e['errors'] for e in report['experiments']) else 0


if __name__ == '__main__':
    sys.exit(main())
