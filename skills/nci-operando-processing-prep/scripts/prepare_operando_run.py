#!/usr/bin/env python3
"""Prepare operando runs by updating config values and summarizing TPX time ranges."""

from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

KEYS_BY_SECTION = {
    'general': ['base_dir', 'echem_folder', 'result_root'],
    'obint_correction.parameters': [
        'exp_dir',
        'beamhistory_path',
        'reference_dir',
        'reference_beamhistory_path',
    ],
}

PATH_KEYS = {
    'base_dir',
    'echem_folder',
    'result_root',
    'exp_dir',
    'beamhistory_path',
    'reference_dir',
    'reference_beamhistory_path',
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Update operando_config.ini paths and summarize TPX time ranges.'
    )
    parser.add_argument('--config', required=True, help='Path to operando_config.ini')
    parser.add_argument('--base-dir')
    parser.add_argument('--echem-folder')
    parser.add_argument('--result-root')
    parser.add_argument('--exp-dir')
    parser.add_argument('--beamhistory-path')
    parser.add_argument('--reference-dir')
    parser.add_argument('--reference-beamhistory-path')
    parser.add_argument('--show-ranges', action='store_true', help='Print TPX time ranges for experiment/ and reference/')
    parser.add_argument('--dry-run', action='store_true', help='Show planned changes without writing the config file')
    parser.add_argument('--print-config', action='store_true', help='Print the resolved config values after applying any requested changes')
    return parser.parse_args()


def read_config_lines(config_path: Path) -> List[str]:
    if not config_path.exists():
        raise FileNotFoundError(f'Config file not found: {config_path}')
    return config_path.read_text(encoding='utf-8').splitlines()


def collect_current_values(lines: Iterable[str]) -> Dict[Tuple[str, str], str]:
    current_section = None
    values: Dict[Tuple[str, str], str] = {}
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith('#') or stripped.startswith(';'):
            continue
        if stripped.startswith('[') and stripped.endswith(']'):
            current_section = stripped[1:-1].strip()
            continue
        if current_section is None or '=' not in raw_line:
            continue
        key, value = raw_line.split('=', 1)
        values[(current_section, key.strip())] = value.strip()
    return values


def apply_updates(lines: List[str], updates: Dict[Tuple[str, str], str]) -> List[str]:
    output: List[str] = []
    current_section = None
    pending = dict(updates)
    section_pattern = re.compile(r'^\[(.+)\]\s*$')

    for raw_line in lines:
        stripped = raw_line.strip()
        match = section_pattern.match(stripped)
        if match:
            current_section = match.group(1).strip()
            output.append(raw_line)
            continue
        if current_section is not None and '=' in raw_line:
            key, _value = raw_line.split('=', 1)
            normalized_key = key.strip()
            update_key = (current_section, normalized_key)
            if update_key in pending:
                output.append(f'{normalized_key} = {pending.pop(update_key)}')
                continue
        output.append(raw_line)

    if pending:
        raise KeyError(
            'Could not find config entries for: ' + ', '.join(f'[{section}] {key}' for section, key in pending)
        )
    return output


def collect_tpx_times(dataset_dir: Path, extension: str = '.tpx3') -> List[datetime]:
    times: List[datetime] = []
    for path in sorted(dataset_dir.rglob(f'*{extension}')):
        try:
            times.append(datetime.fromtimestamp(path.stat().st_mtime))
        except FileNotFoundError:
            continue
    return times


def count_run_dirs(dataset_dir: Path, extension: str = '.tpx3') -> int:
    run_count = 0
    for child in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
        if any(child.rglob(f'*{extension}')):
            run_count += 1
    return run_count


def summarize_tpx_root(root: Path, extension: str = '.tpx3') -> List[Dict[str, object]]:
    if not root.exists():
        return []

    rows: List[Dict[str, object]] = []
    for entry in sorted(p for p in root.iterdir() if p.is_dir()):
        all_times = collect_tpx_times(entry, extension=extension)
        earliest = min(all_times) if all_times else None
        latest = max(all_times) if all_times else None
        duration_hours = ((latest - earliest).total_seconds() / 3600.0) if all_times else None
        rows.append({
            'name': entry.name,
            'run_count': count_run_dirs(entry, extension=extension),
            'tpx_file_count': len(all_times),
            'earliest': earliest,
            'latest': latest,
            'duration_hours': duration_hours,
        })
    return rows


def print_range_table(title: str, rows: List[Dict[str, object]]) -> None:
    print(f'\n{title}:')
    if not rows:
        print('  none found')
        return
    for row in rows:
        earliest = row['earliest'].isoformat(sep=' ', timespec='seconds') if row['earliest'] else 'None'
        latest = row['latest'].isoformat(sep=' ', timespec='seconds') if row['latest'] else 'None'
        duration = f"{row['duration_hours']:.3f}" if row['duration_hours'] is not None else 'None'
        print(
            f"- {row['name']}: runs={row['run_count']}, tpx_files={row['tpx_file_count']}, "
            f"earliest={earliest}, latest={latest}, duration_h={duration}"
        )


def validate_paths(current_values: Dict[Tuple[str, str], str]) -> List[str]:
    issues: List[str] = []
    base_dir = current_values.get(('general', 'base_dir'))
    if base_dir is None:
        issues.append('Missing [general] base_dir')
        return issues

    base_path = Path(base_dir)
    for section, key in current_values:
        if key not in PATH_KEYS or key == 'base_dir':
            continue
        raw = current_values[(section, key)]
        full = Path(raw) if os.path.isabs(raw) else base_path / raw
        if key == 'result_root':
            continue
        if not full.exists():
            issues.append(f'{section}.{key} -> missing: {full}')
    return issues


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    lines = read_config_lines(config_path)
    current = collect_current_values(lines)

    updates: Dict[Tuple[str, str], str] = {}
    arg_map = {
        'base_dir': args.base_dir,
        'echem_folder': args.echem_folder,
        'result_root': args.result_root,
        'exp_dir': args.exp_dir,
        'beamhistory_path': args.beamhistory_path,
        'reference_dir': args.reference_dir,
        'reference_beamhistory_path': args.reference_beamhistory_path,
    }
    for key, value in arg_map.items():
        if value is None:
            continue
        for section, keys in KEYS_BY_SECTION.items():
            if key in keys:
                updates[(section, key)] = value
                break

    changed: List[Tuple[str, str, str | None, str | None]] = []
    new_lines = lines
    if updates:
        new_lines = apply_updates(lines, updates)
        new_current = collect_current_values(new_lines)
        for section_key in updates:
            old = current.get(section_key)
            new = new_current.get(section_key)
            if old != new:
                changed.append((section_key[0], section_key[1], old, new))
        current = new_current

    if args.print_config:
        print('\nCurrent config values:')
        for (section, key), value in sorted(current.items()):
            print(f'- [{section}] {key} = {value}')

    issues = validate_paths(current)
    if issues:
        print('\nPath validation:')
        for issue in issues:
            print(f'- {issue}')
    else:
        print('\nPath validation: all referenced input paths exist.')

    if args.show_ranges:
        base_dir = Path(current[('general', 'base_dir')])
        print_range_table('Experiment TPX time ranges', summarize_tpx_root(base_dir / 'experiment'))
        print_range_table('Reference TPX time ranges', summarize_tpx_root(base_dir / 'reference'))

    if changed:
        heading = 'Planned config changes:' if args.dry_run else 'Applied config changes:'
        print(f'\n{heading}')
        for section, key, old, new in changed:
            print(f'- [{section}] {key}: {old!r} -> {new!r}')
    elif updates:
        print('\nNo config values changed.')

    if updates and not args.dry_run:
        config_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
        print(f'\nWrote {config_path}')
    elif updates and args.dry_run:
        print(f'\nDry run only; {config_path} was not modified.')


if __name__ == '__main__':
    main()
