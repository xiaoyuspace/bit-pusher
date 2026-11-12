import hashlib
import json
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DB = DATA / 'publisher.sqlite3'


@contextmanager
def connect():
    DATA.mkdir(exist_ok=True)
    db = sqlite3.connect(DB, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def init():
    with connect() as db:
        db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,profile_id TEXT,profile_name TEXT,
          handle TEXT,text TEXT,video TEXT,file_hash TEXT,fingerprint TEXT UNIQUE,state TEXT,
          message TEXT,url TEXT DEFAULT '',created REAL,updated REAL);
        CREATE TABLE IF NOT EXISTS published_fingerprints(fingerprint TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY,task_id TEXT,time REAL,message TEXT);
        ''')
        db.execute("UPDATE tasks SET state='unknown',message='服务在提交期间中断；请核实账号时间线，禁止自动重试。' WHERE state='submitting'")
        db.execute("UPDATE tasks SET state='failed',message='服务中断，任务未提交。检查后可重新排队。' WHERE state IN ('preparing','uploading')")
        db.execute("UPDATE tasks SET state='draft',message='服务重启后已恢复草稿，请重新选择执行。' WHERE state='queued'")


def file_hash(path):
    path = Path(path).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError('视频路径必须是存在的本地文件完整路径。')
    if path.suffix.lower() not in ('.mp4', '.mov', '.webm'):
        raise ValueError('请选择 MP4、MOV 或 WebM 视频。')
    if path.stat().st_size == 0:
        raise ValueError('视频文件为空。')
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return str(path.resolve()), digest.hexdigest()


def fingerprint(profile, text, digest):
    return hashlib.sha256(json.dumps([profile, text, digest], ensure_ascii=False).encode()).hexdigest()


def failed_archive(task_id):
    if not re.fullmatch(r'[0-9a-f]{32}', task_id):
        raise ValueError('失败任务编号无效。')
    parent = DATA.resolve() / 'failed'
    target = parent / task_id
    if target.resolve() != target:
        raise ValueError('失败归档路径无效。')
    return target


def archived_video(task):
    try:
        path = failed_archive(task['id']) / Path(task['video']).name
        return path if path.resolve() == path and path.is_file() else None
    except (ValueError, OSError):
        return None


def reassign_failed(task_id, profile_id, profile_name):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task or task['state'] not in ('failed', 'blocked') or task['url']:
            raise ValueError('只可重新分配未发布的失败或受阻任务。')
        archive = archived_video(task)
        if not archive:
            raise ValueError('失败归档中找不到视频。')
        video, digest = file_hash(archive)
        if digest != task['file_hash']:
            raise ValueError('归档视频与原任务不一致。')
        fp = fingerprint(profile_id, task['text'], digest)
        if db.execute('SELECT 1 FROM published_fingerprints WHERE fingerprint=?', (fp,)).fetchone():
            raise ValueError('目标窗口曾提交过相同文案和视频。')
        now = time.time()
        message = '已重新分配，等待使用失败归档发帖。'
        try:
            db.execute('''UPDATE tasks SET profile_id=?,profile_name=?,video=?,fingerprint=?,
                          state='queued',message=?,updated=? WHERE id=?''',
                       (profile_id, profile_name, video, fp, message, now, task_id))
        except sqlite3.IntegrityError:
            raise ValueError('目标窗口已有相同文案和视频的任务。') from None
        db.execute('INSERT INTO logs(task_id,time,message) VALUES(?,?,?)', (task_id, now, message))


def create_task(data, db=None):
    handle = ''  # Retained column for compatibility with existing task databases.
    text = str(data.get('text', '')).strip()
    if not text or len(text) > 25000:
        raise ValueError('请填写文案，最大 25,000 字符；实际长度限制由 X 页面判断。')
    profile = str(data.get('profile_id', ''))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', profile):
        raise ValueError('请选择浏览器窗口。')
    video, digest = file_hash(str(data.get('video', '')).strip().strip('"'))
    fp = fingerprint(profile, text, digest)
    task_id, now = uuid.uuid4().hex, time.time()
    with (connect() if db is None else nullcontext(db)) as db:
        try:
            inserted = db.execute('''INSERT INTO tasks(id,profile_id,profile_name,handle,text,video,file_hash,fingerprint,state,message,created,updated)
                SELECT ?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM published_fingerprints WHERE fingerprint=?)''',
                (task_id,profile,str(data.get('profile_name', profile))[:150],handle,text,video,digest,fp,'draft','任务已保存，等待执行。',now,now,fp))
            if not inserted.rowcount:
                raise ValueError('此窗口曾提交过相同文案和视频，原任务记录已删除。')
        except sqlite3.IntegrityError:
            raise ValueError('该窗口已有相同文案和视频的任务，请使用原任务，避免重复发布。')
    return task_id


def tasks():
    with connect() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM tasks ORDER BY created DESC')]
    for task in rows:
        task['can_reassign'] = (
            task['state'] in ('failed', 'blocked') and not task['url'] and bool(archived_video(task))
        )
    return rows


def get(task_id):
    with connect() as db:
        row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('任务不存在。')
        return dict(row)


def update(task_id, state, message, url=''):
    with connect() as db:
        db.execute('UPDATE tasks SET state=?,message=?,url=CASE WHEN ?="" THEN url ELSE ? END,updated=? WHERE id=?', (state,message,url,url,time.time(),task_id))
        db.execute('INSERT INTO logs(task_id,time,message) VALUES(?,?,?)', (task_id,time.time(),message))
    if state in ('failed', 'blocked'):
        archive_failed(task_id)
    elif state == 'success':
        task = get(task_id)
        if task['url'] and Path(task['video']).parent == DATA.resolve() / 'failed' / task_id:
            try:
                target = failed_archive(task_id)
                if target.is_dir():
                    shutil.rmtree(target)
            except (OSError, ValueError):
                with connect() as db:
                    db.execute("UPDATE tasks SET message=message || ' 失败归档清理未完成。' WHERE id=?", (task_id,))


def enqueue(ids):
    if not isinstance(ids,list) or not ids:
        raise ValueError('请勾选要执行的任务。')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for tid in dict.fromkeys(ids):
            row = db.execute('SELECT state FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row or row['state'] not in ('draft','failed','cancelled','blocked'):
                raise ValueError('只可执行草稿、已取消或提交前失败及受阻的任务。结果待核实任务不能重发。')
            db.execute("UPDATE tasks SET state='queued',message='已排队，等待执行。',updated=? WHERE id=?", (time.time(),tid))


def claim():
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
        if not row:
            return None
        db.execute("UPDATE tasks SET state='preparing',message='准备打开窗口',updated=? WHERE id=?", (time.time(),row['id']))
        return dict(row)


def delete_tasks(ids, current=None):
    if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids):
        raise ValueError('请选择要删除的任务。')
    ids = list(dict.fromkeys(ids))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for tid in ids:
            row = db.execute('SELECT state,fingerprint FROM tasks WHERE id=?',(tid,)).fetchone()
            if tid == current or not row or row['state'] not in ('draft','success','failed','blocked','cancelled','unknown'):
                raise ValueError('所选任务已变更，或仍在排队、执行。请刷新后重新选择。')
            if row['state'] in ('success', 'unknown'):
                db.execute('INSERT OR IGNORE INTO published_fingerprints VALUES (?)',(row['fingerprint'],))
            db.execute('DELETE FROM logs WHERE task_id=?',(tid,))
            db.execute('DELETE FROM tasks WHERE id=?',(tid,))
    return len(ids)


def cancel_queued():
    with connect() as db:
        db.execute("UPDATE tasks SET state='cancelled',message='已停止排队。',updated=? WHERE state='queued'", (time.time(),))


def resolve(task_id, url):
    if not re.fullmatch(r'https://(?:x\.com|twitter\.com)/[A-Za-z0-9_]+/status/[0-9]+', url):
        raise ValueError('请提供核实后的完整帖子链接。')
    if get(task_id)['state'] != 'unknown':
        raise ValueError('仅待核实任务可以手动确认。')
    update(task_id, 'success', '用户已人工核实发布成功。', url)


def archive_failed(task_id):
    task = get(task_id)
    try:
        target = failed_archive(task_id)
        target.mkdir(parents=True, exist_ok=True)
        (target / '文案.txt').write_text(task['text'], encoding='utf-8-sig')
        (target / '失败原因.txt').write_text(task['profile_name'] + '\n' + task['message'], encoding='utf-8-sig')
        (target / '任务.json').write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding='utf-8')
        source = Path(task['video'])
        destination = target / source.name
        if source.is_file() and (not destination.exists() or destination.stat().st_size != source.stat().st_size):
            temporary = target / (source.name + '.copying')
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        elif not source.is_file():
            raise FileNotFoundError('原视频已不存在')
    except Exception as exc:
        with connect() as db:
            db.execute('UPDATE tasks SET message=message || ? WHERE id=?', ('；失败素材归档未完成：' + str(exc)[:200], task_id))
