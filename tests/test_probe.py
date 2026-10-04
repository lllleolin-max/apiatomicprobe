import copy
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest

from apiatomicprobe import InputError, analyze, collect
from apiatomicprobe.model import check
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

    def test_witness_clock_and_every_control_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Lab(root / 'app.db') as app:
                directory = root / 'records'
                collect(app.url, app.database, app.nonce, plan([[operation(key='late', delay_ms=100), operation(key='early')]]), directory, authorized=True)
            path = directory / 'witness-0000.json'; receipt = json.loads(path.read_bytes())
            fence = json.loads((directory / 'control-0002.observation.json').read_bytes())['response_ns']
            for offset in (1, 0):
                receipt['captured_ns'] = fence + offset; path.write_bytes(canonical(receipt))
                self.assertEqual(analyze(directory)['status'], 'PASS')
            receipt['captured_ns'] = fence - 1; path.write_bytes(canonical(receipt))
            with self.assertRaises(InputError):
                analyze(directory)

    def test_completion_counts_type_and_total_nanosecond_edges(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Lab(root / 'app.db') as app:
                directory = root / 'records'; declared = plan([[operation()]])
                collect(app.url, app.database, app.nonce, declared, directory, authorized=True)
            path = directory / 'complete.json'; original = json.loads(path.read_bytes())
            for count in (0, 2, True):
                changed = dict(original, operations=count); path.write_bytes(canonical(changed))
                with self.assertRaises(InputError):
                    analyze(directory)
            start = json.loads((directory / 'session.json').read_bytes())['start_ns']
            for offset in (-1, 0, 1):
                changed = dict(original, end_ns=start + declared['max_duration_ms']*1000000 + offset)
                path.write_bytes(canonical(changed))
                self.assertEqual(analyze(directory)['status'], 'UNKNOWN' if offset == 1 else 'PASS')
            path.write_bytes(b'{')
            self.assertEqual(analyze(directory)['status'], 'UNKNOWN')

    def test_known_response_cap_is_unknown_at_actual_bytes_minus_one(self):
        size = len(canonical(dict(order_id='ord-00000001', tenant='alpha', sku='widget', amount=100)))
        for allowance in (size - 1, size, size + 1):
            result = self.actual('atomic', [[operation()]], max_response_bytes=allowance)
            self.assertEqual(result['status'], 'UNKNOWN' if allowance < size else 'PASS')

    def test_pending_must_share_one_explanation_across_native_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Lab(root / 'app.db') as app:
                collect(app.url, app.database, app.nonce, plan([[operation(drop_ack=True)], [operation()]]), root / 'records', authorized=True)
            directory = root / 'records'
            initial = json.loads((directory / 'initial.json').read_bytes())['state']
            first = json.loads((directory / 'witness-0000.json').read_bytes())
            final = json.loads((directory / 'witness-0001.json').read_bytes())
            changed = copy.deepcopy(final['state'])
            changed['orders'][0]['amount'] = 200; changed['effects'][0]['delta'] = -200
            changed['accounts'][0]['balance'] = 9800
            op = operation(amount=200)
            from apiatomicprobe.protocol import request_body, sha
            changed['inbox'][0]['body_sha256'] = sha(request_body(op))
            response = canonical(dict(order_id='ord-00000001', tenant='alpha', sku='widget', amount=200))
            changed['inbox'][0]['response'] = response.decode()
            first_inv = json.loads((directory / 'op-0000.invocation.json').read_bytes())
            second_inv = json.loads((directory / 'op-0001.invocation.json').read_bytes())
            second_obs = json.loads((directory / 'op-0001.observation.json').read_bytes())
            entries = [dict(operation=operation(drop_ack=True), invocation_ns=first_inv['invocation_ns'], response_ns=None, status=None, response_hex=''),
                       dict(operation=op, invocation_ns=second_inv['invocation_ns'], response_ns=second_obs['response_ns'], status=201, response_hex=response.hex())]
            checkpoints = [dict(state=first['state'], begin_ns=first['captured_ns'], captured_ns=first['captured_ns']),
                           dict(state=changed, begin_ns=final['captured_ns'], captured_ns=final['captured_ns'])]
            self.assertEqual(check(initial, entries, changed, checkpoints=checkpoints)['status'], 'COUNTEREXAMPLE')

    def test_declared_pause_cannot_dispatch_after_total_allowance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with Lab(root / 'app.db') as app:
                declared = plan([[operation()]])
                declared['max_duration_ms'] = 500; declared['waves'][0]['pause_ms'] = 1000
                result = collect(app.url, app.database, app.nonce, declared, root / 'records', authorized=True)
                self.assertEqual(result['status'], 'UNKNOWN')
                connection = sqlite3.connect(app.database)
                try:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM requests WHERE path='/orders'").fetchone()[0], 0)
                finally:
                    connection.close()


if __name__ == '__main__':
    unittest.main()
