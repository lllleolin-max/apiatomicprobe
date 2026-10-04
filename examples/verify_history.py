"""Standalone factorial oracle/native SQL consumer, imports no project package.

Small histories only. Deliberately no DFS memo, mask search or product parser.
Enumerate every pending inclusion choice, linear order and native final order id.
"""
import copy
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import sys


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def witness_rows(state):
    return {table: sorted(state[table], key=encode) for table in ('accounts', 'orders', 'effects', 'inbox')}


def exhaustive(initial, entries, final, checkpoints=()):
    if len(entries) > 8:
        raise ValueError('independent factorial oracle is limited to8 operations')
    operation_count = len(entries)
    entries = list(entries) + [dict(kind='witness', call=c['begin_ns'], **{'return': c['captured_ns']}, state=c['state'], fenced_operations=c['operations']) for c in checkpoints]
    pending = [i for i, entry in enumerate(entries) if entry['return'] is None]
    mandatory = set(range(len(entries))) - set(pending)
    attempts = 0
    def apply(state, entry, status, response):
        op = entry['op']
        params = hashlib.sha256(encode({'sku': op['sku'], 'amount': op['amount']})).hexdigest()
        previous = [row for row in state['inbox'] if row['tenant'] == op['tenant'] and row['idem_key'] == op['key']]
        if previous:
            cached = previous[0]
            expected = (409, encode({'error': 'key_conflict'})) if cached['body_sha256'] != params else (cached['status'], cached['response'].encode())
            return state if expected == (status, response) else None
        balances = {row['tenant']: row['balance'] for row in state['accounts']}
        if op['tenant'] not in balances:
            return None
        changed = copy.deepcopy(state)
        if balances[op['tenant']] < op['amount']:
            if (status, response) != (402, encode({'error': 'insufficient_funds'})):
                return None
        else:
            if status != 201:
                return None
            answer = json.loads(response)
            if set(answer) != {'order_id', 'tenant', 'sku', 'amount'} or any(answer[field] != op[field] for field in ('tenant', 'sku', 'amount')):
                return None
            if any(row['order_id'] == answer['order_id'] for row in changed['orders']):
                return None
            changed['orders'].append({'order_id': answer['order_id'], 'tenant': op['tenant'], 'idem_key': op['key'], 'sku': op['sku'], 'amount': op['amount']})
            changed['effects'].append({'order_id': answer['order_id'], 'tenant': op['tenant'], 'delta': -op['amount']})
            for account in changed['accounts']:
                if account['tenant'] == op['tenant']:
                    account['balance'] -= op['amount']
        changed['inbox'].append({'tenant': op['tenant'], 'idem_key': op['key'], 'body_sha256': params, 'status': status, 'response': response.decode()})
        return changed
    for inclusion in itertools.product((False, True), repeat=len(pending)):
        selected = mandatory | {index for index, include in zip(pending, inclusion) if include}
        for order in itertools.permutations(selected):
            position = {index: rank for rank, index in enumerate(order)}
            if any(entries[a]['return'] is not None and entries[a]['return'] < entries[b]['call'] and position[a] > position[b] for a in selected for b in selected):
                continue
            if any(position[op] > position[read] for read in selected if entries[read].get('kind') == 'witness'
                   for op in selected if op < operation_count and op < entries[read]['fenced_operations']):
                continue
            states = [copy.deepcopy(initial)]
            for index in order:
                entry = entries[index]
                if entry.get('kind') == 'witness':
                    states = [state for state in states if witness_rows(state) == witness_rows(entry['state'])]
                    continue
                next_states = []
                for state in states:
                    if entry['return'] is not None:
                        options = [(entry['status'], entry['raw'])]
                    else:
                        op = entry['op']
                        previous = [row for row in state['inbox'] if (row['tenant'], row['idem_key']) == (op['tenant'], op['key'])]
                        options = [(402, encode({'error': 'insufficient_funds'})), (409, encode({'error': 'key_conflict'}))]
                        options += [(row['status'], row['response'].encode()) for row in previous]
                        options += [(201, encode({key: row[key] for key in ('order_id', 'tenant', 'sku', 'amount')})) for row in final['orders']]
                    for status, body in options:
                        attempts += 1
                        changed = apply(state, entry, status, body)
                        if changed is not None:
                            next_states.append(changed)
                states = next_states
            if any(witness_rows(state) == witness_rows(final) for state in states):
                return dict(status='PASS', oracle_transition_attempts=attempts)
    return dict(status='COUNTEREXAMPLE', oracle_transition_attempts=attempts)


def consume(directory):
    directory = Path(directory)
    declared = json.loads((directory / 'plan.json').read_bytes())
    initial = json.loads((directory / 'initial.json').read_bytes())['state']
    entries = []
    prefixes = []
    total = 0
    final = None
    checkpoints = []
    for wave in range(len(declared['waves'])):
        witness = json.loads((directory / f'witness-{wave:04d}.json').read_bytes())
        for op in declared['waves'][wave]['operations']:
            index = len(entries)
            invocation = json.loads((directory / f'op-{index:04d}.invocation.json').read_bytes())
            observation = json.loads((directory / f'op-{index:04d}.observation.json').read_bytes())
            original_request = (directory / f'op-{index:04d}.request').read_bytes()
            raw = (directory / f'op-{index:04d}.response').read_bytes()
            assert json.loads(original_request) == {'sku': op['sku'], 'amount': op['amount']}
            assert hashlib.sha256(original_request).hexdigest() == invocation['request_sha256']
            assert len(raw) == observation['response_bytes']
            assert hashlib.sha256(raw).hexdigest() == observation['response_sha256']
            total += len(raw) + len(original_request)
            assert initial['nonce'] == witness['state']['nonce']
            assert invocation['invocation_ns'] <= observation['observation_ns'] <= witness['captured_ns']
            if observation['response_ns'] is not None:
                json.loads(raw)  # Consume full supported original bytes, no digest-only answer.
            entries.append(dict(op=op, call=invocation['invocation_ns'], return_=observation['response_ns'], status=observation['status'], raw=raw))
            entries[-1]['return'] = entries[-1].pop('return_')
        final = witness['state']
        fence = json.loads((directory / f'control-{2+2*wave:04d}.observation.json').read_bytes())['response_ns']
        checkpoints.append(dict(begin_ns=fence, captured_ns=witness['captured_ns'], state=final, operations=len(entries)))
        prefixes.append(exhaustive(initial, entries, final, checkpoints))
    report = json.loads((directory / 'report.json').read_bytes())
    first_invalid = next((i for i, p in enumerate(prefixes) if p['status'] == 'COUNTEREXAMPLE'), None)
    truth = 'PASS' if first_invalid is None else 'COUNTEREXAMPLE'
    assert report['status'] in (truth, 'UNKNOWN'), (directory, truth, report['status'])
    # The target's native SQL state is the final consumer truth, independently of
    # status codes and irrespective of checker early-prefix stopping.
    database = directory.parent / (directory.name + '.db')
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('BEGIN')
        columns = {'accounts': ['tenant', 'balance'], 'orders': ['order_id', 'tenant', 'idem_key', 'sku', 'amount'],
                   'effects': ['order_id', 'tenant', 'delta'], 'inbox': ['tenant', 'idem_key', 'body_sha256', 'status', 'response']}
        native = {table: sorted([dict(zip(fields, row)) for row in connection.execute('SELECT ' + ','.join(fields) + ' FROM ' + table)], key=encode) for table, fields in columns.items()}
        assert native == witness_rows(final)
        assert sum(row['balance'] for row in native['accounts']) == 20000 + sum(row['delta'] for row in native['effects'])
        assert {row['order_id'] for row in native['orders']} == {row['order_id'] for row in native['effects']}
        assert sum(row['amount'] for row in native['orders']) == -sum(row['delta'] for row in native['effects'])
        requests = list(connection.execute('SELECT path,tenant,idem_key,body FROM requests ORDER BY seq'))
        order_requests = [row for row in requests if row[0] == '/orders']
        assert len(order_requests) == len(entries)
        planned_requests = sorted([(e['op']['tenant'], e['op']['key'], encode({'sku': e['op']['sku'], 'amount': e['op']['amount']})) for e in entries])
        assert sorted([(tenant, key, bytes(body)) for path, tenant, key, body in order_requests]) == planned_requests
        assert len(requests) - len(order_requests) == 1 + 2 * len(declared['waves'])
        for control_record in directory.glob('control-*.observation.json'):
            index = control_record.name.split('.')[0]
            obs = json.loads(control_record.read_bytes())
            raw = (directory / (index + '.response')).read_bytes()
            assert len(raw) == obs['response_bytes'] and hashlib.sha256(raw).hexdigest() == obs['response_sha256']
            total += len(raw)
    finally:
        connection.close()
    return dict(directory=str(directory), product_status=report['status'], oracle_status=truth, earliest_invalid_wave=first_invalid,
                actual_operation_requests=len(entries), pending_operations=sum(e['return'] is None for e in entries),
                complete_original_request_response_bytes=total, native_orders=len(native['orders']), native_effects=len(native['effects']),
                native_balance_sum=sum(row['balance'] for row in native['accounts']), actual_server_http_requests=len(requests),
                actual_server_control_requests=len(requests) - len(order_requests), prefixes=prefixes)


if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve()
    results = [consume(path / policy) for path in sorted(root.iterdir()) if path.is_dir() for policy in ('concurrent', 'serial')]
    print(json.dumps(results, indent=2))
