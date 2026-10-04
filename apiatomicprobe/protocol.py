"""The deliberately narrow order-lab/v1 adapter and bounded native witness."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class InputError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InputError('duplicate JSON member')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(InputError('non-finite JSON')))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise InputError('invalid supported JSON') from error


def keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise InputError('unsupported object members')


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise InputError(f'{name} outside supported integer range')
    return value


def name(value):
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value) is None:
        raise InputError('identifier must be 1..64 ASCII name characters')
    return value


def request_body(op):
    return canonical(dict(sku=op['sku'], amount=op['amount']))


def validate_operation(op):
    keys(op, ('tenant', 'key', 'sku', 'amount', 'drop_ack', 'delay_ms'))
    for field in ('tenant', 'key', 'sku'):
        name(op[field])
    integer(op['amount'], 'amount', 1, 1000000)
    integer(op['delay_ms'], 'delay_ms', 0, 1000)
    if type(op['drop_ack']) is not bool:
        raise InputError('drop_ack must be boolean')


def validate_plan(plan):
    keys(plan, ('format', 'waves', 'timeout_ms', 'max_duration_ms', 'max_response_bytes', 'search_nodes'))
    if plan['format'] != 'apiatomicprobe-plan/v1':
        raise InputError('unsupported plan version')
    integer(plan['timeout_ms'], 'timeout_ms', 1, 10000)
    integer(plan['max_duration_ms'], 'max_duration_ms', 1, 60000)
    integer(plan['max_response_bytes'], 'max_response_bytes', 1, 1048576)
    integer(plan['search_nodes'], 'search_nodes', 1, 1000000)
    if not isinstance(plan['waves'], list) or not 1 <= len(plan['waves']) <= 12:
        raise InputError('1..12 waves required')
    count = 0
    for wave in plan['waves']:
        keys(wave, ('pause_ms', 'operations'))
        integer(wave['pause_ms'], 'pause_ms', 0, 1000)
        if not isinstance(wave['operations'], list) or not 1 <= len(wave['operations']) <= 12:
            raise InputError('1..12 operations per wave required')
        count += len(wave['operations'])
        for op in wave['operations']:
            validate_operation(op)
    if count > 12:
        raise InputError('at most 12 operations per finite history')
    return plan


def read_json(path, limit=1048576):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > limit:
        raise InputError('input is symlink or exceeds byte allowance')
    data = path.read_bytes()
    if len(data) > limit:
        raise InputError('input exceeded byte allowance')
    return parse(data)


def preflight(db, output):
    db, output = Path(db).resolve(), Path(output).resolve()
    if not db.is_file():
        raise InputError('witness database must already exist')
    family = [db] + [Path(str(db) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    for item in family:
        if output == item or output in item.parents or item in output.parents:
            raise InputError('output collides with source database or sidecar')
    if output.exists():
        raise InputError('output must be a new directory')
    return db, output


def snapshot(db, nonce, row_cap=1000, byte_cap=1048576):
    """Caller has quiesced the dedicated target; read all tables in one view."""
    connection = None
    try:
        connection = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        meta = dict(connection.execute('SELECT name,value FROM metadata'))
        if meta.get('protocol') != 'order-lab/v1' or meta.get('nonce') != nonce:
            raise InputError('database is not the explicitly authorized order-lab session')
        result = dict(protocol='order-lab/v1', nonce=nonce, retention_seconds=int(meta['retention_seconds']))
        columns = {
            'accounts': ('tenant', 'balance'),
            'orders': ('order_id', 'tenant', 'idem_key', 'sku', 'amount'),
            'effects': ('order_id', 'tenant', 'delta'),
            'inbox': ('tenant', 'idem_key', 'body_sha256', 'status', 'response'),
        }
        total = 0
        for table, names in columns.items():
            if connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] > row_cap:
                raise InputError('witness row allowance exceeded')
            raw_size = connection.execute(f'SELECT COALESCE(SUM({"+".join("COALESCE(length(CAST(" + field + " AS BLOB)),0)" for field in names)}),0) FROM {table}').fetchone()[0]
            if raw_size > byte_cap:
                raise InputError('witness material exceeds byte allowance before fetching')
            rows = [dict(zip(names, row)) for row in connection.execute(f'SELECT {",".join(names)} FROM {table}')]
            result[table] = sorted(rows, key=canonical)
            total += len(canonical(rows))
            if total > byte_cap:
                raise InputError('witness byte allowance exceeded')
        validate_state(result)
        return result
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise InputError('unsupported or unreadable native witness') from error
    finally:
        if connection is not None:
            connection.close()


def validate_state(state):
    keys(state, ('protocol', 'nonce', 'retention_seconds', 'accounts', 'orders', 'effects', 'inbox'))
    if state['protocol'] != 'order-lab/v1' or not isinstance(state['nonce'], str):
        raise InputError('unsupported witness identity')
    integer(state['retention_seconds'], 'retention_seconds', 1, 3600)
    schema = {'accounts': ('tenant', 'balance'), 'orders': ('order_id', 'tenant', 'idem_key', 'sku', 'amount'),
              'effects': ('order_id', 'tenant', 'delta'), 'inbox': ('tenant', 'idem_key', 'body_sha256', 'status', 'response')}
    for table, fields in schema.items():
        if not isinstance(state[table], list) or len(state[table]) > 1000:
            raise InputError('unsupported witness rows')
        seen = set()
        for row in state[table]:
            keys(row, fields)
            name(row['tenant'])
            identity = (row['tenant'], row['idem_key']) if table == 'inbox' else row.get('order_id', row['tenant'])
            if identity in seen:
                raise InputError('duplicate native witness identity')
            seen.add(identity)
            if table == 'accounts':
                integer(row['balance'], 'balance', 0, 1000000000)
            elif table == 'orders':
                name(row['order_id']); name(row['idem_key']); name(row['sku'])
                integer(row['amount'], 'amount', 1, 1000000)
            elif table == 'effects':
                name(row['order_id']); integer(row['delta'], 'delta', -1000000, -1)
            else:
                name(row['idem_key'])
                if not isinstance(row['body_sha256'], str) or re.fullmatch('[0-9a-f]{64}', row['body_sha256']) is None:
                    raise InputError('invalid request digest')
                if row['status'] not in (201, 402) or type(row['status']) is not int:
                    raise InputError('unsupported cached status')
                if not isinstance(row['response'], str):
                    raise InputError('cached response must be original UTF8 text')
                validate_response(row['status'], row['response'].encode('utf-8'))
    if len(canonical(state)) > 1048576:
        raise InputError('witness exceeds total byte allowance')


def validate_response(status, raw):
    body = parse(raw)
    if status == 201:
        keys(body, ('order_id', 'tenant', 'sku', 'amount'))
        for field in ('order_id', 'tenant', 'sku'):
            name(body[field])
        integer(body['amount'], 'amount', 1, 1000000)
    elif status in (402, 409):
        keys(body, ('error',))
        if body['error'] != {402: 'insufficient_funds', 409: 'key_conflict'}[status]:
            raise InputError('unexpected supported error body')
    else:
        raise InputError('HTTP status is outside order-lab model')
    if canonical(body) != raw:
        raise InputError('adapter requires canonical original response bytes')
    return body
