"""A real order/balance HTTP application, including deliberately faulty targets."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sqlite3
import threading
import time
import uuid

from .protocol import InputError, canonical, keys, name, parse, sha, integer


SCHEMA = '''
CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE accounts(tenant TEXT PRIMARY KEY,balance INTEGER NOT NULL);
CREATE TABLE orders(order_id TEXT PRIMARY KEY,tenant TEXT NOT NULL,idem_key TEXT NOT NULL,sku TEXT NOT NULL,amount INTEGER NOT NULL);
CREATE TABLE effects(order_id TEXT PRIMARY KEY,tenant TEXT NOT NULL,delta INTEGER NOT NULL);
CREATE TABLE inbox(tenant TEXT NOT NULL,idem_key TEXT NOT NULL,body_sha256 TEXT NOT NULL,status INTEGER NOT NULL,response TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(tenant,idem_key));
CREATE TABLE requests(seq INTEGER PRIMARY KEY,path TEXT NOT NULL,tenant TEXT,idem_key TEXT,body BLOB NOT NULL);
'''


class Lab:
    def __init__(self, database, *, mode='atomic'):
        if mode not in ('atomic', 'racy', 'scope', 'ttl'):
            raise InputError('unsupported lab target mode')
        self.database = Path(database).resolve()
        if self.database.exists():
            raise InputError('lab database must be new')
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.nonce = uuid.uuid4().hex
        self.condition = threading.Condition()
        self.accepting = False
        self.active = 0
        self.race = threading.Barrier(2)
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript(SCHEMA)
            connection.executemany('INSERT INTO metadata VALUES (?,?)', [('protocol', 'order-lab/v1'), ('nonce', self.nonce), ('retention_seconds', '300')])
            connection.executemany('INSERT INTO accounts VALUES (?,?)', [('alpha', 10000), ('beta', 10000)])
            connection.commit()
        finally:
            connection.close()
        lab = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass
            def respond(self, status, body):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (OSError, ConnectionError):
                    pass
                self.close_connection = True
            def do_POST(self):
                if self.headers.get('X-Lab-Nonce') != lab.nonce:
                    self.respond(403, canonical({'error': 'unauthorized_session'})); return
                if self.path in ('/quiesce', '/resume'):
                    lab.record_request(self.path, None, None, b'')
                    with lab.condition:
                        if self.path == '/quiesce':
                            lab.accepting = False
                            while lab.active:
                                lab.condition.wait()
                        else:
                            lab.accepting = True
                    self.respond(200, canonical({'nonce': lab.nonce, 'state': self.path[1:]})); return
                if self.path != '/orders':
                    self.respond(404, canonical({'error': 'unsupported_path'})); return
                with lab.condition:
                    if not lab.accepting:
                        self.respond(503, canonical({'error': 'quiesced'})); return
                    lab.active += 1
                try:
                    length = int(self.headers.get('Content-Length', '-1'))
                    if not 0 < length <= 4096:
                        raise InputError('body allowance')
                    raw = self.rfile.read(length)
                    lab.record_request(self.path, self.headers.get('X-Tenant'), self.headers.get('Idempotency-Key'), raw)
                    body = parse(raw); keys(body, ('sku', 'amount'))
                    name(body['sku']); integer(body['amount'], 'amount', 1, 1000000)
                    tenant, key = name(self.headers.get('X-Tenant')), name(self.headers.get('Idempotency-Key'))
                    delay = integer(int(self.headers.get('X-Delay-Ms', '0')), 'delay', 0, 1000)
                    if delay:
                        time.sleep(delay / 1000)
                    status, response = lab.execute(tenant, key, body)
                    if self.headers.get('X-Drop-Ack') == 'yes':
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self.respond(status, response)
                except (ValueError, InputError, sqlite3.Error) as error:
                    self.respond(400, canonical({'error': type(error).__name__}))
                finally:
                    with lab.condition:
                        lab.active -= 1
                        lab.condition.notify_all()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def record_request(self, path, tenant, key, raw):
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            connection.execute('INSERT INTO requests(path,tenant,idem_key,body) VALUES (?,?,?,?)', (path, tenant, key, raw))
            connection.commit()
        finally:
            connection.close()

    def execute(self, tenant, key, body):
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            storage_tenant = 'alpha' if self.mode == 'scope' else tenant
            body_hash = sha(canonical(body))
            # Racy target checks outside the business transaction. This defect is
            # in the application under test, not deliberately in the checker.
            if self.mode == 'racy':
                cached = connection.execute('SELECT body_sha256,status,response FROM inbox WHERE tenant=? AND idem_key=?', (storage_tenant, key)).fetchone()
                if cached is None:
                    try:
                        self.race.wait(timeout=.3)
                    except threading.BrokenBarrierError:
                        pass
                connection.execute('BEGIN IMMEDIATE')
            else:
                connection.execute('BEGIN IMMEDIATE')
                if self.mode == 'ttl':
                    connection.execute('DELETE FROM inbox WHERE created<?', (time.monotonic() - .01,))
                cached = connection.execute('SELECT body_sha256,status,response FROM inbox WHERE tenant=? AND idem_key=?', (storage_tenant, key)).fetchone()
            if cached:
                connection.commit()
                return (409, canonical({'error': 'key_conflict'})) if cached[0] != body_hash else (cached[1], cached[2].encode('utf-8'))
            balance = connection.execute('SELECT balance FROM accounts WHERE tenant=?', (tenant,)).fetchone()
            if balance is None:
                raise InputError('unknown tenant')
            if balance[0] < body['amount']:
                status, raw = 402, canonical({'error': 'insufficient_funds'})
            else:
                order_id = f"ord-{connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0]+1:08d}"
                status, raw = 201, canonical(dict(order_id=order_id, tenant=tenant, sku=body['sku'], amount=body['amount']))
                connection.execute('INSERT INTO orders VALUES (?,?,?,?,?)', (order_id, tenant, key, body['sku'], body['amount']))
                connection.execute('INSERT INTO effects VALUES (?,?,?)', (order_id, tenant, -body['amount']))
                connection.execute('UPDATE accounts SET balance=balance-? WHERE tenant=?', (body['amount'], tenant))
            connection.execute('INSERT OR REPLACE INTO inbox VALUES (?,?,?,?,?,?)', (storage_tenant, key, body_hash, status, raw.decode('utf-8'), time.monotonic()))
            connection.commit()
            return status, raw
        finally:
            connection.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
