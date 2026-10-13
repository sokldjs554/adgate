"""Real HTTP and a new OS process, sharing only the persisted SQLite file."""
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
from test_api import ALICE, BOB, CLEAN, REVIEWABLE


@contextmanager
def server(db):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'adgate.api:create_app', '--factory',
                             '--host', '127.0.0.1', '--port', str(port), '--log-level', 'warning'],
                            env={**os.environ, 'ADGATE_DEV':'1', 'ADGATE_DB':str(db)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get('/healthz').status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(.05)
            else:
                raise AssertionError('Server failed to start')
            yield client
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_process_restart_preserves_reviews_rewrites_lineage_events_and_idempotency(tmp_path):
    db = tmp_path/'restart.sqlite3'
    payload = {**CLEAN, 'headline':'생활자금 대출 안내', 'advertiser':{'name':'가상대출','category':'loan','verified':True}}
    with server(db) as c:
        parent = c.post('/v1/reviews', json=payload, headers=ALICE).json()
        rid = parent['run_id']
        rewrite = c.post(f'/v1/reviews/{rid}/rewrite', headers=ALICE).json()
        assert rewrite['status'] == 'verified'
        headers = {**ALICE, 'X-Parent-Run':rid, 'Idempotency-Key':'restart-child'}
        child = c.post('/v1/reviews', json=rewrite['candidate'], headers=headers).json()
        waiting = c.post('/v1/reviews', json=REVIEWABLE, headers=ALICE).json()
        assert waiting['state'] == 'waiting_input'
        decided = c.post(f"/v1/reviews/{waiting['run_id']}/resume", headers=BOB,
                         json={'verdict':'REJECT','reason':'기간 근거가 확인되지 않았습니다.'}).json()
        paths = [f'/v1/reviews/{i}' for i in (rid, child['run_id'], decided['run_id'])]
        paths += [f'{p}/events' for p in paths] + [f'/v1/reviews/{rid}/rewrite']
        snapshots = {p:c.get(p,headers=ALICE).json() for p in paths}
    with server(db) as c:
        assert snapshots == {p:c.get(p,headers=ALICE).json() for p in paths}
        replay = c.post('/v1/reviews',json=rewrite['candidate'],headers=headers)
        assert replay.status_code == 200 and replay.json()['run_id'] == child['run_id']
        assert c.post(f"/v1/reviews/{decided['run_id']}/resume",headers=BOB,
                      json={'verdict':'APPROVE','reason':'중복 결정을 시도합니다.'}).status_code == 409
