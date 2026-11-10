"""Share window states without touching the source checkout or exporting credentials."""
import hashlib
import json
import math
import os
import re
import subprocess
import time

import store

REF = 'refs/heads/window-state'
FILE = 'window-state.json'
STATES = ('pending', 'ready', 'delete')
status = '等待窗口状态同步。'
revision = 0


def git(directory, *args, input_text=None):
    result = subprocess.run(
        ['git', '-C', str(directory), *args],
        input=input_text.encode('utf-8') if input_text is not None else None,
        capture_output=True, timeout=20,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    result.check_returncode()
    return result.stdout.decode('utf-8')


def key(pid):
    return hashlib.sha256(pid.encode('utf-8')).hexdigest()


def decode(raw):
    records = json.loads(raw)
    if not isinstance(records, dict):
        raise ValueError('Invalid snapshot')
    for fingerprint, entry in records.items():
        if (not re.fullmatch(r'[0-9a-f]{64}', fingerprint)
                or not isinstance(entry, list) or len(entry) != 2
                or entry[0] not in STATES or type(entry[1]) not in (int, float)
                or not math.isfinite(entry[1]) or entry[1] <= 0):
            raise ValueError('Invalid window state')
    return records


def sync_once():
    global revision
    from window_manager import init
    from worker import profiles

    remote = git(store.ROOT, 'remote', 'get-url', 'origin').strip()
    directory = store.DATA / 'window-sync'
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / '.git').is_dir():
        git(directory, 'init', '--quiet')

    parent = None
    remote_records = {}
    if git(directory, 'ls-remote', '--refs', remote, REF).strip():
        git(directory, 'fetch', '--quiet', '--no-tags', remote, REF)
        parent = git(directory, 'rev-parse', 'FETCH_HEAD').strip()
        remote_records = decode(git(directory, 'show', f'{parent}:{FILE}'))

    init()
    try:
        ids = {profile['id'] for profile in profiles()}
    except Exception:
        # Existing records can still be shared while BitBrowser is offline.
        ids = None
    merged = remote_records.copy()
    imported = False
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        local = {row['id']: row for row in db.execute('SELECT id, state, checked FROM windows')}
        for pid in ids if ids is not None else local:
            fingerprint = key(pid)
            row = local.get(pid)
            shared = remote_records.get(fingerprint)
            if shared and (row is None or shared[1] > (row['checked'] or 0)):
                db.execute('INSERT OR REPLACE INTO windows VALUES(?,?,?,?)',
                           (pid, shared[0], '从仓库同步的窗口检测状态。', shared[1]))
                imported = True
            elif row and row['state'] in STATES and row['checked'] and (
                not shared or row['checked'] > shared[1]
            ):
                merged[fingerprint] = [row['state'], row['checked']]
    if imported:
        revision += 1

    if merged != remote_records:
        snapshot = json.dumps(merged, sort_keys=True, indent=2) + '\n'
        blob = git(directory, 'hash-object', '-w', '--stdin', input_text=snapshot).strip()
        tree = git(directory, 'mktree', input_text=f'100644 blob {blob}\t{FILE}\n').strip()
        parents = ('-p', parent) if parent else ()
        commit = git(directory, '-c', 'user.name=Bit Publisher',
                     '-c', 'user.email=bit-publisher@users.noreply.github.com',
                     'commit-tree', tree, *parents, '-m', 'Sync window states').strip()
        git(directory, 'push', '--quiet', remote, f'{commit}:{REF}')


def run():
    global status
    while True:
        try:
            sync_once()
            status = '窗口状态已同步 · ' + time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            status = '窗口状态同步未完成，将自动重试。请检查网络和 GitHub 仓库权限。'
        time.sleep(60)
