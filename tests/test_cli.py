"""Subprocess checks use synthetic fixtures and never open a user's Hermes home."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args, **env):
        return subprocess.run([sys.executable, '-X', 'utf8', str(ROOT/'czip_cli.py'), *args],
                              capture_output=True, text=True, encoding='utf-8',
                              env={**os.environ, **env}, timeout=15)

    def test_existing_help_retained(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('czip', result.stdout.lower())

    def test_missing_index_does_not_create_or_invent_results(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/'missing.db')
            result = self.run_cli('find', 'udf', '--index', path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'index_missing')
            self.assertFalse(Path(path).exists())

    def test_explicit_index_and_source_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home = root/'home'; home.mkdir()
            system = root/'system'; plans = system/'ceo-planlar'; plans.mkdir(parents=True)
            (plans/'demo.md').write_text('UDF açma düzeltmesi 2026/123', encoding='utf-8')
            index = str(root/'index.db')
            result = self.run_cli('find-index', '--home', str(home), '--system', str(system), '--index', index)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_cli('find', 'uyap', '--case', '2026/123', CZIP_JOB_INDEX=index)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'found')

    def test_drive_requires_explicit_remote(self):
        result = self.run_cli('find-drive')
        self.assertEqual(result.returncode, 2)
        self.assertIn('requires --remote', result.stderr)
