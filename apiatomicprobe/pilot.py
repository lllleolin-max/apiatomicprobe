"""Business CSV→real disposable HTTP orders→SQL consumer comparison."""
import csv
from pathlib import Path
import time

from .lab import Lab
from .protocol import canonical, InputError, validate_plan
from .runner import collect, persist


def operation(tenant='alpha', key='sale-1', sku='widget', amount=100, drop_ack=False, delay_ms=0):
    return dict(tenant=tenant, key=key, sku=sku, amount=amount, drop_ack=drop_ack, delay_ms=delay_ms)


def plan(waves, *, search_nodes=100000, timeout_ms=1000):
    return dict(format='apiatomicprobe-plan/v1', waves=[dict(pause_ms=30 if i else 0, operations=wave) for i, wave in enumerate(waves)],
                timeout_ms=timeout_ms, max_duration_ms=15000, max_response_bytes=4096, search_nodes=search_nodes)


def csv_operations(path):
    with open(path, encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ['tenant', 'key', 'sku', 'amount']:
            raise InputError('expected business CSV columns tenant,key,sku,amount')
        operations = [operation(row['tenant'], row['key'], row['sku'], int(row['amount'])) for row in reader]
    validate_plan(plan([operations]))
    return operations


def run_pilot(output):
    root = Path(output).resolve()
    if root.exists():
        raise InputError('pilot output must be new')
    root.mkdir(parents=True)
    (root / 'orders.csv').write_text('tenant,key,sku,amount\nalpha,sale-1,widget,100\nalpha,sale-1,widget,100\n', encoding='utf-8')
    duplicate = csv_operations(root / 'orders.csv')
    cases = [
        ('atomic', 'atomic', [duplicate, [operation(amount=101)], [operation(tenant='beta')]], 100000),
        ('race', 'racy', [duplicate], 100000),
        ('scope', 'scope', [[operation()], [operation(tenant='beta')]], 100000),
        ('early-ttl', 'ttl', [[operation()], [operation()]], 100000),
        ('lost-ack', 'atomic', [[operation(drop_ack=True)], [operation()]], 100000),
        ('pending-timeout', 'atomic', [[operation(delay_ms=200)], [operation()]], 100000),
        ('search-unknown', 'atomic', [duplicate], 1),
        ('no-race', 'racy', [[operation()]], 100000),
    ]
    records = []
    for label, mode, waves, budget in cases:
        case = root / label; case.mkdir()
        preparation = time.monotonic()
        with Lab(case / 'concurrent.db', mode=mode) as app:
            prepare_seconds = time.monotonic() - preparation
            declared = plan(waves, search_nodes=budget, timeout_ms=20 if label == 'pending-timeout' else 1000)
            start = time.monotonic()
            result = collect(app.url, app.database, app.nonce, declared, case / 'concurrent', authorized=True)
            concurrent_seconds = time.monotonic() - start
        # Same semantic input/fault knobs, fully sequential control. Key retention,
        # preparation, HTTP operation count and all native effects remain visible.
        with Lab(case / 'serial.db', mode=mode) as app:
            serial_waves = [[op] for wave in waves for op in wave]
            start = time.monotonic()
            serial = collect(app.url, app.database, app.nonce, plan(serial_waves, search_nodes=budget, timeout_ms=declared['timeout_ms']), case / 'serial', authorized=True)
            serial_seconds = time.monotonic() - start
        records.append(dict(case=label, target_mode=mode, status=result['status'], serial_status=serial['status'],
            operation_http_requests=sum(map(len, waves)), control_http_requests_per_complete_run=1 + 2 * len(waves),
            serial_control_http_requests_per_complete_run=1 + 2 * sum(map(len, waves)), prepare_seconds=prepare_seconds,
            concurrent_seconds=concurrent_seconds, serial_seconds=serial_seconds,
            scope='synthetic local order application; timings include capture/search/fsync, not API throughput'))
    summary = dict(format='apiatomicprobe-pilot/v1', synthetic_lab=True, customer_adoption='unknown', production_savings='unmeasured', cases=records)
    persist(root / 'pilot.json', canonical(summary))
    return summary
