"""Bounded real-time search; pending completion is constrained by native effects."""
import copy

from .protocol import InputError, canonical, request_body, sha, validate_response, validate_state


def transition(state, op, status, raw):
    body_hash = sha(request_body(op))
    current = next((row for row in state['inbox'] if (row['tenant'], row['idem_key']) == (op['tenant'], op['key'])), None)
    if current:
        expected = (409, canonical({'error': 'key_conflict'})) if current['body_sha256'] != body_hash else (current['status'], current['response'].encode('utf-8'))
        return state if (status, raw) == expected else None
    account = next((row for row in state['accounts'] if row['tenant'] == op['tenant']), None)
    if account is None:
        return None
    result = copy.deepcopy(state)
    if account['balance'] < op['amount']:
        if (status, raw) != (402, canonical({'error': 'insufficient_funds'})):
            return None
    else:
        if status != 201:
            return None
        body = validate_response(status, raw)
        if (body['tenant'], body['sku'], body['amount']) != (op['tenant'], op['sku'], op['amount']):
            return None
        if any(row['order_id'] == body['order_id'] for row in result['orders']):
            return None
        result['orders'].append(dict(order_id=body['order_id'], tenant=op['tenant'], idem_key=op['key'], sku=op['sku'], amount=op['amount']))
        result['effects'].append(dict(order_id=body['order_id'], tenant=op['tenant'], delta=-op['amount']))
        next(row for row in result['accounts'] if row['tenant'] == op['tenant'])['balance'] -= op['amount']
    result['inbox'].append(dict(tenant=op['tenant'], idem_key=op['key'], body_sha256=body_hash, status=status, response=raw.decode('utf-8')))
    return result


def normalized(state):
    return canonical({key: sorted(value, key=canonical) if isinstance(value, list) else value for key, value in state.items()})


def alternatives(state, entry, final):
    if entry['response_ns'] is not None:
        return [(entry['status'], bytes.fromhex(entry['response_hex']))]
    op = entry['operation']
    current = next((row for row in state['inbox'] if (row['tenant'], row['idem_key']) == (op['tenant'], op['key'])), None)
    if current:
        return [(409, canonical({'error': 'key_conflict'}))] if current['body_sha256'] != sha(request_body(op)) else [(current['status'], current['response'].encode('utf-8'))]
    options = [(402, canonical({'error': 'insufficient_funds'}))]
    for row in final['orders']:
        if (row['tenant'], row['idem_key'], row['sku'], row['amount']) == (op['tenant'], op['key'], op['sku'], op['amount']):
            options.append((201, canonical({key: row[key] for key in ('order_id', 'tenant', 'sku', 'amount')})))
    return options


def check(initial, entries, final, *, search_nodes=100000, checkpoints=()):
    validate_state(initial); validate_state(final)
    if initial['nonce'] != final['nonce'] or initial['retention_seconds'] != final['retention_seconds']:
        raise InputError('witness representation changed')
    if not 0 <= len(entries) <= 12:
        raise InputError('finite model supports at most12 operations')
    events = [dict(entry, kind='operation', index=index) for index, entry in enumerate(entries)]
    for index, checkpoint in enumerate(checkpoints):
        events.append(dict(kind='witness', index=index, invocation_ns=checkpoint['begin_ns'], response_ns=checkpoint['captured_ns'], state=checkpoint['state'],
                           fenced_operations=checkpoint['operations']))
    predecessors = [sum(1 << j for j, previous in enumerate(events)
                        if previous['response_ns'] is not None and previous['response_ns'] < entry['invocation_ns']) for entry in events]
    for i, event in enumerate(events):
        if event['kind'] == 'witness':
            predecessors[i] |= (1 << event['fenced_operations']) - 1
    nodes = 0
    exhausted = False
    memo = set()
    target = normalized(final)
    def search(mask, state, path):
        nonlocal nodes, exhausted
        if nodes >= search_nodes:
            exhausted = True
            return None
        nodes += 1
        signature = (mask, normalized(state))
        if signature in memo:
            return None
        memo.add(signature)
        if mask == (1 << len(events)) - 1:
            return path if signature[1] == target else None
        for i, entry in enumerate(events):
            bit = 1 << i
            if mask & bit or predecessors[i] & ~mask:
                continue
            if entry['kind'] == 'witness':
                if normalized(state) != normalized(entry['state']):
                    continue
                witness = search(mask | bit, state, path + [dict(witness=entry['index'], disposition='native-read')])
                if witness is not None:
                    return witness
                continue
            if entry['response_ns'] is None:
                witness = search(mask | bit, state, path + [dict(index=i, disposition='omitted')])
                if witness is not None:
                    return witness
            for status, raw in alternatives(state, entry, final):
                changed = transition(state, entry['operation'], status, raw)
                if changed is None:
                    continue
                witness = search(mask | bit, changed, path + [dict(index=i, disposition='completed', status=status, response_hex=raw.hex())])
                if witness is not None:
                    return witness
        return None
    witness = search(0, initial, [])
    return dict(status='PASS' if witness is not None else 'UNKNOWN' if exhausted else 'COUNTEREXAMPLE', search_nodes=nodes,
                search_exhausted=exhausted, linearization=witness, finite_history_only=True)
