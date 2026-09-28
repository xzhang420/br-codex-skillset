#!/usr/bin/env python3
"""Behavioral fixtures; all data is created in disposable temporary directories."""

import json
from pathlib import Path
import tempfile
import unittest

from audit_proposal import audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proposal = Path(self.tmp.name)
        self.root = self.proposal / 'data' / 'experiment'
        self.root.mkdir(parents=True)

    def put(self, section, experiment, tail, content=b''):
        p = self.root / section / experiment / tail
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def manifest(self, name, states, status='in_progress', grouped=False):
        target = 'test_focus/' + name if grouped else name
        return self.put('metadata', target, 'experiment.json', json.dumps({
            'experiment': name, 'expected_runs': list(states),
            'runs': {r: {'status': s} for r, s in states.items()}, 'status': status,
        }).encode())

    def experiment(self, name):
        return next(e for e in audit(self.proposal)['experiments'] if e['experiment'] == name)

    def test_mixed_runs_and_final_images(self):
        name = 'exp001'; done = name + '_00000'; failed = name + '_00001'; absent = name + '_00002'
        self.manifest(name, {done: 'completed', failed: 'failed', absent: 'planned'})
        image = self.put('final', name, '5to15/' + done + '_sumImage_5to15.tiff', b'image')
        self.put('metadata', name, failed + '/acquisition.json', b'{}')
        self.put('logs', name, failed + '/log.txt', b'failed')
        e = self.experiment(name); runs = {r['run']: r for r in e['runs']}
        self.assertEqual(runs[done]['flags'], [])
        self.assertEqual(runs[done]['bytes'], 5)
        self.assertEqual(runs[done]['paths'][0]['path'], str(image))
        self.assertIn('no_local_payload', runs[failed]['flags'])
        self.assertEqual(runs[failed]['bytes'], 8)
        self.assertIn('no_local_paths', runs[absent]['flags'])
        self.assertEqual(runs[absent]['paths'], [])
        self.assertIn('mixed_completed_and_other_runs', e['flags'])

    def test_recovery_data_is_not_empty(self):
        name = 'exp002'; run = name + '_00000'
        self.manifest(name, {run: 'acquired'})
        self.put('.work', name, run + '/incoming/chunk.tpx3', b'partial-data')
        r = self.experiment(name)['runs'][0]
        self.assertEqual(r['flags'], ['unfinished_processing'])
        self.assertEqual(r['payload_bytes'], 12)

    def test_completed_missing_local_data_is_protected(self):
        name = 'exp003'; run = name + '_00000'
        self.manifest(name, {run: 'completed'}, 'completed')
        r = self.experiment(name)['runs'][0]
        self.assertIn('completed_but_no_local_payload', r['flags'])
        self.assertNotIn('no_local_payload', r['flags'])

    def test_group_all_sections_archives_and_preserve_shared(self):
        name = 'TEST004'; run = name + '_00000'
        self.manifest(name, {run: 'processed'}, 'completed')
        for s in ['final', 'tpx3Files', 'logs', '.work', 'rawFiles', 'customOutputs']:
            self.put(s, name, run + '/file', b'data')
        archive = self.root / 'tpx3Files' / (name + '.tar.gz'); archive.write_bytes(b'archive')
        self.put('logs', 'serval', 'server.log', b'shared')
        self.put('.work', 'batch_focus_cache', 'file', b'shared')
        e = self.experiment(name)
        self.assertEqual(len(e['group_moves']), 8)
        self.assertEqual(e['archives'], [str(archive)])
        self.assertTrue(all('/test_focus/' in m['destination'] for m in e['group_moves']))
        self.assertEqual([e['experiment'] for e in audit(self.proposal)['experiments']], [name])

    def test_already_grouped_and_conflict(self):
        name = 'Focus005'; run = name + '_00000'
        self.manifest(name, {run: 'failed'}, grouped=True)
        self.put('final', 'test_focus/' + name, run + '/file', b'image')
        self.assertEqual(self.experiment(name)['group_moves'], [])
        self.put('final', name, run + '/file', b'other')
        e = self.experiment(name)
        self.assertTrue(e['errors'])
        self.assertTrue(e['group_moves'][0]['conflict'])

    def test_missing_malformed_manifests_and_zero_byte_tree(self):
        self.put('tpx3Files', 'exp006', 'exp006_00000/empty.tpx3')
        self.put('metadata', 'exp007', 'experiment.json', b'{broken')
        e = self.experiment('exp006')
        self.assertIn('unknown_status', e['flags'])
        self.assertIn('zero_byte_tree', e['flags'])
        self.assertIsNone(e['expected_runs'])
        self.assertTrue(self.experiment('exp007')['manifest_error'])

    def test_archive_does_not_get_attributed_to_one_run(self):
        name = 'exp008'; run = name + '_00000'
        self.manifest(name, {run: 'failed'})
        p = self.root / 'tpx3Files'; p.mkdir()
        (p / (name + '_part1_00000-00005.tar.gz')).write_bytes(b'archive')
        e = self.experiment(name)
        self.assertEqual(len(e['archives']), 1)
        self.assertIn('archive_coverage_unknown', e['runs'][0]['flags'])
        self.assertEqual(e['runs'][0]['bytes'], 0)

    def test_symlink_is_not_followed(self):
        name = 'exp009'; run = name + '_00000'
        self.manifest(name, {run: 'failed'})
        outside = self.proposal / 'outside'; outside.write_bytes(b'valuable')
        p = self.root / 'metadata' / name / 'linked'; p.symlink_to(outside)
        e = self.experiment(name)
        self.assertTrue(e['errors'])
        self.assertEqual(outside.read_bytes(), b'valuable')
        self.assertEqual(e['files'], 1)

    def test_ambiguous_layout_and_root_symlink(self):
        (self.proposal / 'data' / 'experiments').mkdir()
        with self.assertRaises(ValueError): audit(self.proposal)
        (self.proposal / 'data' / 'experiments').rmdir()
        link = self.proposal / 'link'; link.symlink_to(self.proposal)
        with self.assertRaises(ValueError): audit(link)

    def test_run_prefix_and_aggregate_are_preserved(self):
        name = 'exp010'; short = name + '_00001'; long = name + '_000010'
        self.manifest(name, {short: 'failed', long: 'completed'})
        self.put('final', name, 'default/' + long + '_image.tiff', b'long')
        self.put('final', name, 'default/' + name + '_sumAllRuns.tiff', b'sum')
        runs = {r['run']: r for r in self.experiment(name)['runs']}
        self.assertEqual(runs[short]['paths'], [])
        self.assertEqual(runs[long]['bytes'], 4)

    def test_read_only_snapshot_and_custom_unmapped(self):
        name = 'exp011'; self.manifest(name, {name + '_00000': 'planned'})
        self.put('custom', 'mystery', 'file', b'keep')
        def snapshot():
            return {str(p): (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                    for p in self.proposal.rglob('*')}
        before = snapshot(); report = audit(self.proposal, include_inventory=True)
        self.assertEqual(before, snapshot())
        self.assertTrue(report['experiments'][0]['inventory'])
        self.assertTrue(any('mystery' in w for w in report['warnings']))


if __name__ == '__main__':
    unittest.main()
