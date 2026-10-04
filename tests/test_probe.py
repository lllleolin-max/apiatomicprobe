import copy
from pathlib import Path
import tempfile
import unittest

from apiatomicprobe import InputError, analyze, collect
from apiatomicprobe.lab import Lab
from apiatomicprobe.pilot import operation, plan
from apiatomicprobe.protocol import canonical, parse, preflight, snapshot, validate_plan


class ProbeTests(unittest.TestCase):
    def actual(self, mode, waves, **changes):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Lab(root / 'app.db', mode=mode) as app:
                declared = plan(waves); declared.update(changes)
                result = collect(app.url, app.database, app.nonce, declared, root / 'records', authorized=True)
                self.assertEqual(analyze(root / 'records'), result)
                return result

    def test_atomic_same_key_conflicting_body_other_tenant(self):
        result = self.actual('atomic', [[operation(), operation()], [operation(amount=101)], [operation(tenant='beta')]])
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(result['complete'])

    def test_check_then_act_real_race_is_counterexample_serial_is_pass(self):
        self.assertEqual(self.actual('racy', [[operation(), operation()]])['status'], 'COUNTEREXAMPLE')
        self.assertEqual(self.actual('racy', [[operation()], [operation()]])['status'], 'PASS')

    def test_cross_tenant_scope_and_early_ttl(self):
        self.assertEqual(self.actual('scope', [[operation()], [operation(tenant='beta')]])['status'], 'COUNTEREXAMPLE')
        self.assertEqual(self.actual('ttl', [[operation()], [operation()]])['status'], 'COUNTEREXAMPLE')

    def test_unknown_ack_is_once_and_timeout_is_not_return(self):
        self.assertEqual(self.actual('atomic', [[operation(drop_ack=True)], [operation()]])['status'], 'PASS')
        result = self.actual('atomic', [[operation(delay_ms=100)], [operation()]], timeout_ms=10)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['prefixes'][0]['linearization'][0]['disposition'], 'completed')

    def test_one_node_budget_does_not_mean_counterexample(self):
        result = self.actual('atomic', [[operation(), operation()]], search_nodes=1)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertTrue(result['prefixes'][0]['search_exhausted'])

    def test_bad_inputs_and_source_family_collisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Lab(root / 'app.db') as app:
                for output in [app.database, Path(str(app.database) + '-wal'), app.database.parent]:
                    with self.assertRaises(InputError):
                        collect(app.url, app.database, app.nonce, plan([[operation()]]), output, authorized=True)
                self.assertFalse(Path(str(app.database) + '-wal').exists())
                with self.assertRaises(InputError):
                    collect(app.url, app.database, app.nonce, plan([[operation()]]), root / 'no-auth')
                self.assertFalse((root / 'no-auth').exists())
                with self.assertRaises(InputError):
                    collect('http://example.com', app.database, app.nonce, plan([[operation()]]), root / 'external', authorized=True)
                self.assertFalse((root / 'external').exists())

    def test_plan_bounds_and_json_duplicates(self):
        declared = plan([[operation()]])
        for value in (0, 1000001, True, 10**500):
            changed = copy.deepcopy(declared); changed['search_nodes'] = value
            with self.assertRaises(InputError):
                validate_plan(changed)
        with self.assertRaises(InputError):
            parse(b'{"a":1,"a":2}')

    def test_raw_corruption_is_controlled_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Lab(root / 'app.db') as app:
                collect(app.url, app.database, app.nonce, plan([[operation()]]), root / 'records', authorized=True)
            (root / 'records/op-0000.response').write_bytes(b'changed')
            with self.assertRaises(InputError):
                analyze(root / 'records')


if __name__ == '__main__':
    unittest.main()
