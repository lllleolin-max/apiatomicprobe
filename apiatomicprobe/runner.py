"""Durable client-clock HTTP records and native SQL witness acquisition."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection, HTTPException
from pathlib import Path
import os
import re
import threading
import time
from urllib.parse import urlsplit

from .model import check
from .protocol import (InputError, canonical, integer, keys, parse, preflight, read_json, request_body, sha,
                       snapshot, validate_operation, validate_plan, validate_response, validate_state)


def persist(path, data):
    with open(path, 'xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def endpoint(url):
    if not isinstance(url, str):
        raise InputError('URL must be text')
    try:
        split = urlsplit(url)
        if split.scheme != 'http' or split.hostname not in ('127.0.0.1', '::1') or split.username or split.password or split.path not in ('', '/') or split.query or split.fragment or split.port is None:
            raise InputError('only explicit loopback HTTP order-lab origin is supported')
        return split.hostname, split.port
    except ValueError as error:
        raise InputError('invalid loopback origin') from error


def call(origin, path, nonce, raw, headers, timeout, cap):
    connection = HTTPConnection(*origin, timeout=timeout)
    try:
        connection.request('POST', path, raw, headers={'X-Lab-Nonce': nonce, 'Content-Type': 'application/json', 'Connection': 'close', **headers})
        response = connection.getresponse()
        declared = response.getheader('Content-Length')
        if declared is None or re.fullmatch(r'[0-9]+', declared) is None or int(declared) > cap:
            raise InputError('response framing/byte allowance unsupported')
        body = response.read(cap + 1)
        if len(body) != int(declared) or len(body) > cap:
            raise InputError('truncated or oversized response')
        return response.status, body, response.getheaders()
    finally:
        connection.close()


def control(origin, action, nonce, timeout):
    status, raw, _ = call(origin, '/' + action, nonce, b'', {}, timeout, 4096)
    if status != 200 or parse(raw) != dict(nonce=nonce, state=action):
        raise InputError('target did not acknowledge quiesce/resume session')


def collect(url, database, nonce, plan, output, *, authorized=False):
    if authorized is not True:
        raise InputError('explicit disposable-test-target authorization required')
    origin = endpoint(url)
    validate_plan(plan)
    if not isinstance(nonce, str) or re.fullmatch('[0-9a-f]{32}', nonce) is None:
        raise InputError('explicit lab session nonce required')
    db, directory = preflight(database, output)
    # Refuse wrong session/schema before creating output or submitting any HTTP.
    snapshot(db, nonce)
    directory.mkdir(parents=True)
    timeout = plan['timeout_ms'] / 1000
    start = time.monotonic_ns()
    deadline = start + plan['max_duration_ms'] * 1000000
    persist(directory / 'plan.json', canonical(plan))
    persist(directory / 'session.json', canonical(dict(format='apiatomicprobe-session/v1', url=url, database=str(db), nonce=nonce, start_ns=start)))
    initial = None
    controls = 0
    def captured_control(action):
        nonlocal controls
        prefix = directory / f'control-{controls:04d}'
        controls += 1
        invoked = time.monotonic_ns()
        persist(Path(str(prefix) + '.request'), b'')
        persist(Path(str(prefix) + '.invocation.json'), canonical(dict(action=action, invocation_ns=invoked)))
        status, raw, headers = call(origin, '/' + action, nonce, b'', {}, max(timeout, 2), 4096)
        returned = time.monotonic_ns()
        persist(Path(str(prefix) + '.response'), raw)
        persist(Path(str(prefix) + '.observation.json'), canonical(dict(status=status, response_ns=returned, response_bytes=len(raw), response_sha256=sha(raw), headers=headers)))
        if status != 200 or parse(raw) != dict(nonce=nonce, state=action):
            raise InputError('target did not acknowledge quiesce/resume session')
    try:
        captured_control('quiesce')
        initial = snapshot(db, nonce)
        persist(directory / 'initial.json', canonical(dict(state=initial, captured_ns=time.monotonic_ns())))
        ordinal = 0
        for wave_index, wave in enumerate(plan['waves']):
            if time.monotonic_ns() >= deadline:
                raise InputError('collection time allowance exhausted')
            if wave['pause_ms']:
                time.sleep(wave['pause_ms'] / 1000)
            captured_control('resume')
            barrier = threading.Barrier(len(wave['operations']))
            def execute(index, operation):
                barrier.wait()
                body = request_body(operation)
                invocation = time.monotonic_ns()
                persist(directory / f'op-{index:04d}.request', body)
                persist(directory / f'op-{index:04d}.invocation.json', canonical(dict(index=index, wave=wave_index, operation=operation, invocation_ns=invocation, request_sha256=sha(body))))
                response_ns = None
                raw = b''
                status = None
                headers = []
                error = None
                try:
                    status, raw, headers = call(origin, '/orders', nonce, body, {'X-Tenant': operation['tenant'], 'Idempotency-Key': operation['key'],
                        'X-Drop-Ack': 'yes' if operation['drop_ack'] else 'no', 'X-Delay-Ms': str(operation['delay_ms'])}, timeout, plan['max_response_bytes'])
                    response_ns = time.monotonic_ns()
                except (OSError, HTTPException, InputError) as failure:
                    error = type(failure).__name__ + ': ' + str(failure)
                persist(directory / f'op-{index:04d}.response', raw)
                persist(directory / f'op-{index:04d}.observation.json', canonical(dict(index=index, response_ns=response_ns, observation_ns=time.monotonic_ns(), status=status,
                    response_sha256=sha(raw), response_bytes=len(raw), response_headers=headers, error=error)))
            with ThreadPoolExecutor(max_workers=len(wave['operations'])) as pool:
                jobs = [pool.submit(execute, ordinal + position, operation) for position, operation in enumerate(wave['operations'])]
                for job in jobs:
                    job.result()
            ordinal += len(wave['operations'])
            # Timeout/closed socket is not server completion. Drain before SQL view.
            captured_control('quiesce')
            state = snapshot(db, nonce)
            persist(directory / f'witness-{wave_index:04d}.json', canonical(dict(state=state, captured_ns=time.monotonic_ns(), operations=ordinal)))
        persist(directory / 'complete.json', canonical(dict(operations=ordinal, waves=len(plan['waves']), end_ns=time.monotonic_ns())))
    except (OSError, HTTPException, InputError) as failure:
        persist(directory / 'collection-error.json', canonical(dict(error=str(failure), observed_ns=time.monotonic_ns())))
    result = analyze(directory)
    persist(directory / 'report.json', canonical(result))
    return result


def analyze(directory):
    directory = Path(directory).resolve()
    plan = validate_plan(read_json(directory / 'plan.json'))
    session = read_json(directory / 'session.json')
    keys(session, ('format', 'url', 'database', 'nonce', 'start_ns'))
    if session['format'] != 'apiatomicprobe-session/v1':
        raise InputError('unsupported session version')
    endpoint(session['url'])
    integer(session['start_ns'], 'start_ns', 0, 2**63-1)
    completion = None
    if (directory / 'complete.json').is_file():
        try:
            completion = read_json(directory / 'complete.json')
        except InputError:
            return dict(status='UNKNOWN', reason='malformed or interrupted completion marker', finite_history_only=True)
        keys(completion, ('operations', 'waves', 'end_ns'))
        integer(completion['operations'], 'completed operations', 0, 12)
        integer(completion['waves'], 'completed waves', 0, 12)
        integer(completion['end_ns'], 'completion end_ns', session['start_ns'], 2**63-1)
        if completion['operations'] != sum(len(w['operations']) for w in plan['waves']) or completion['waves'] != len(plan['waves']):
            raise InputError('completion counts differ from the complete declared schedule')
    if not (directory / 'initial.json').is_file():
        return dict(status='UNKNOWN', reason='no initial native witness', finite_history_only=True)
    def control_receipt(index, action, lower):
        prefix = directory / f'control-{index:04d}'
        inv = read_json(Path(str(prefix) + '.invocation.json'))
        obs = read_json(Path(str(prefix) + '.observation.json'))
        keys(inv, ('action', 'invocation_ns'))
        keys(obs, ('status', 'response_ns', 'response_bytes', 'response_sha256', 'headers'))
        if inv['action'] != action or obs['status'] != 200 or type(obs['status']) is not int:
            raise InputError('unsupported quiesce/resume receipt')
        integer(inv['invocation_ns'], 'control invocation_ns', lower, 2**63-1)
        integer(obs['response_ns'], 'control response_ns', inv['invocation_ns'], 2**63-1)
        integer(obs['response_bytes'], 'control response_bytes', 0, 4096)
        response = Path(str(prefix) + '.response')
        if response.stat().st_size > 4096 or Path(str(prefix) + '.request').read_bytes() != b'':
            raise InputError('unsupported raw control bytes')
        raw = response.read_bytes()
        if len(raw) != obs['response_bytes'] or sha(raw) != obs['response_sha256'] or raw != canonical(dict(nonce=session['nonce'], state=action)):
            raise InputError('quiesce/resume response differs from complete recorded bytes')
        return obs['response_ns']
    initial_receipt = read_json(directory / 'initial.json')
    keys(initial_receipt, ('state', 'captured_ns'))
    integer(initial_receipt['captured_ns'], 'initial captured_ns', control_receipt(0, 'quiesce', session['start_ns']), 2**63-1)
    if completion is not None:
        integer(completion['end_ns'], 'completion end_ns', initial_receipt['captured_ns'], 2**63-1)
    initial = initial_receipt['state']; validate_state(initial)
    if initial['nonce'] != session['nonce']:
        raise InputError('initial witness session mismatch')
    entries = []
    results = []
    remaining_nodes = plan['search_nodes']
    planned = [op for wave in plan['waves'] for op in wave['operations']]
    unknown = None
    previous_capture = initial_receipt['captured_ns']
    for wave_index, wave in enumerate(plan['waves']):
        if not (directory / f'witness-{wave_index:04d}.json').is_file():
            unknown = 'missing quiesced terminal witness'; break
        resumed_ns = control_receipt(1 + 2 * wave_index, 'resume', previous_capture)
        latest_observation = resumed_ns
        for op in wave['operations']:
            index = len(entries)
            inv = read_json(directory / f'op-{index:04d}.invocation.json')
            obs = read_json(directory / f'op-{index:04d}.observation.json')
            keys(inv, ('index', 'wave', 'operation', 'invocation_ns', 'request_sha256'))
            keys(obs, ('index', 'response_ns', 'observation_ns', 'status', 'response_sha256', 'response_bytes', 'response_headers', 'error'))
            if inv['index'] != index or inv['wave'] != wave_index or inv['operation'] != op or obs['index'] != index:
                raise InputError('operation outside declared complete schedule')
            request_path = directory / f'op-{index:04d}.request'
            if request_path.stat().st_size > 4096:
                raise InputError('raw request exceeds adapter byte allowance')
            raw_request = request_path.read_bytes()
            if raw_request != request_body(op) or sha(raw_request) != inv['request_sha256']:
                raise InputError('request differs from planned original bytes')
            response_path = directory / f'op-{index:04d}.response'
            if response_path.stat().st_size > plan['max_response_bytes']:
                raise InputError('raw response exceeds declared byte allowance')
            raw = response_path.read_bytes()
            integer(obs['response_bytes'], 'response_bytes', 0, plan['max_response_bytes'])
            if len(raw) != obs['response_bytes'] or sha(raw) != obs['response_sha256']:
                raise InputError('response differs from recorded complete bytes')
            integer(inv['invocation_ns'], 'invocation_ns', resumed_ns, 2**63-1)
            integer(obs['observation_ns'], 'observation_ns', inv['invocation_ns'], 2**63-1)
            latest_observation = max(latest_observation, obs['observation_ns'])
            if obs['response_ns'] is not None:
                integer(obs['response_ns'], 'response_ns', inv['invocation_ns'], obs['observation_ns'])
                try:
                    validate_response(obs['status'], raw)
                except InputError:
                    unknown = 'completed HTTP response outside supported order-lab semantics'
                if obs['error'] is not None:
                    raise InputError('completed response also claims an observation failure')
            elif obs['status'] is not None or raw or not isinstance(obs['error'], str):
                raise InputError('inconsistent pending observation')
            elif obs['error'].startswith(('InputError:', 'BadStatusLine:', 'IncompleteRead:', 'LineTooLong:', 'HTTPException:')):
                unknown = 'observed HTTP protocol/framing/byte allowance is outside supported semantics'
            entries.append(dict(operation=op, invocation_ns=inv['invocation_ns'], response_ns=obs['response_ns'], status=obs['status'], response_hex=raw.hex()))
        witness = read_json(directory / f'witness-{wave_index:04d}.json')
        keys(witness, ('state', 'captured_ns', 'operations'))
        quiesced_ns = control_receipt(2 + 2 * wave_index, 'quiesce', latest_observation)
        integer(witness['captured_ns'], 'captured_ns', quiesced_ns, 2**63-1)
        previous_capture = witness['captured_ns']
        if completion is not None:
            integer(completion['end_ns'], 'completion end_ns', previous_capture, 2**63-1)
        if witness['operations'] != len(entries):
            raise InputError('witness operation prefix mismatch')
        validate_state(witness['state'])
        if (witness['captured_ns'] - initial_receipt['captured_ns']) > initial['retention_seconds'] * 10**9:
            unknown = 'history exceeds declared no-expiry retention model'
        if witness['captured_ns'] - session['start_ns'] > plan['max_duration_ms'] * 1000000 or (completion is not None and completion['end_ns'] - session['start_ns'] > plan['max_duration_ms'] * 1000000):
            unknown = 'collection time allowance exceeded'
        if unknown:
            results.append(dict(status='UNKNOWN', reason=unknown, operations=len(entries))); break
        result = check(initial, entries, witness['state'], search_nodes=remaining_nodes)
        remaining_nodes -= result['search_nodes']
        result.update(operations=len(entries), wave=wave_index)
        results.append(result)
        if result['status'] != 'PASS':
            break
    complete = completion is not None and len(entries) == len(planned) and len(results) == len(plan['waves'])
    status = results[-1]['status'] if results else 'UNKNOWN'
    if status == 'PASS' and not complete:
        status = 'UNKNOWN'
    return dict(format='apiatomicprobe-report/v1', status=status, complete=complete, observed_operations=len(entries), planned_operations=len(planned),
                prefixes=results, total_search_nodes=plan['search_nodes'] - remaining_nodes, reason=unknown, finite_history_only=True,
                counterexample_scope='earliest failing captured wave prefix, not arbitrary-subset minimum')
