"""실제 Docker/DB 호출 없이 Failback의 로컬 안전 분기만 검증."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('failback', Path(__file__).with_name('rehearsal-failback.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SafetyTests(unittest.TestCase):
    def test_source_must_be_fenced(self):
        with patch.object(module, 'source', return_value='0\t0\n'):
            with self.assertRaisesRegex(RuntimeError, 'Fence'):
                module.fence_check()

    def test_active_source_transaction_blocks(self):
        with patch.object(module, 'source', side_effect=['1\t1\n', '1\n']):
            with self.assertRaisesRegex(RuntimeError, 'Transaction'):
                module.fence_check()

    def test_switch_requires_completed_restore(self):
        with patch.object(module, 'history', return_value=[]), patch.object(module, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, '복원 검증'):
                module.switch_reopen()
            run.assert_not_called()

    def test_no_rds_abort_after_source_enable_or_target_stop(self):
        for event in ['source_write_enable_requested', 'target_backend_stop_requested']:
            with self.subTest(event=event), patch.object(module, 'history', return_value=[{'event': event}]), \
                 patch.object(module, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, '경계 초과'):
                    module.abort_to_rds()
                run.assert_not_called()

    def test_repeated_freeze_rejected(self):
        with patch.object(module, 'history', return_value=[{'event': 'freeze_requested'}]), \
             patch.object(module, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, '재실행 금지'):
                module.freeze_restore()
            run.assert_not_called()

    def test_checksum_null_rejected(self):
        rows = '\n'.join('seonjalal_develop.' + name + '\tNULL' for name in module.TABLES)
        with self.assertRaisesRegex(RuntimeError, '체크섬'):
            module.checksums(lambda sql: rows)

    def test_checksum_missing_table_rejected(self):
        with self.assertRaisesRegex(RuntimeError, '누락'):
            module.checksums(lambda sql: 'seonjalal_develop.products\t123\n')

    def test_tampered_configuration_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'test.conf').write_bytes(b'changed')
            (work / 'prepared-config-hashes.json').write_text(json.dumps({'test.conf': 'wrong'}))
            with patch.object(module, 'WORK', work):
                with self.assertRaisesRegex(RuntimeError, '해시'):
                    module.config('test.conf')

    def test_nginx_syntax_failure_restores_file_without_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            active = work / 'active.conf'
            active.write_bytes(b'old')
            for name, content in [('old.conf', b'old'), ('new.conf', b'new')]:
                (work / name).write_bytes(content)
            (work / 'prepared-config-hashes.json').write_text(json.dumps({
                name: hashlib.sha256(content).hexdigest()
                for name, content in [('old.conf', b'old'), ('new.conf', b'new')]
            }))
            with patch.object(module, 'WORK', work), patch.object(module, 'ACTIVE', active), \
                 patch.object(module, 'workers', return_value={'123'}), \
                 patch.object(module, 'run', side_effect=RuntimeError('syntax failure')) as run:
                with self.assertRaisesRegex(RuntimeError, 'syntax failure'):
                    module.reload_to('old.conf', 'new.conf', 'test')
                self.assertEqual(active.read_bytes(), b'old')
                self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
