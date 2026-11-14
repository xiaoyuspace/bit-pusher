import hashlib
import json
import re
from pathlib import Path
import store


def natural_key(path):
    return [int(s) if s.isdigit() else s.casefold() for s in re.split(r'(\d+)', path.name)]


def plan(folder, text_file, selected, available, generated=''):
    root = Path(str(folder).strip().strip('"')).expanduser()
    text = Path(str(text_file).strip().strip('"')).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('请选择存在的视频文件夹完整路径。')
    if not isinstance(generated, str) or len(generated.encode()) > 5_000_000:
        raise ValueError('生成文案超过 5 MB。')
    if generated:
        raw = generated
    else:
        if not text.is_absolute() or not text.is_file() or text.stat().st_size > 5_000_000:
            raise ValueError('请选择小于 5 MB 的文案 TXT 文件完整路径。')
        try:
            raw = text.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            raw = text.read_text(encoding='gb18030')
    texts = [s.strip() for s in re.split(r'(?m)^\s*===\s*$', raw)]
    if not texts or any(not t for t in texts):
        raise ValueError('文案包含空条目，请检查开头、结尾和连续的 === 分隔行。')
    videos = sorted([p for p in root.iterdir() if p.is_file() and p.suffix.lower() in ('.mp4','.mov','.webm')], key=lambda p:(natural_key(p),p.name))
    if not isinstance(selected,list) or len(set(selected)) != len(selected):
        raise ValueError('窗口选择无效或有重复。')
    profiles = sorted([p for p in available if p['id'] in selected], key=lambda p:int(p.get('seq') or 0))
    if not videos or len(videos) != len(texts) or len(videos) != len(profiles) or len(profiles) != len(selected):
        raise ValueError(f'数量必须一致：视频 {len(videos)} 个，文案 {len(texts)} 条，所选有效窗口 {len(profiles)} 个。')
    if len(videos)>200:
        raise ValueError('每批最多 200 条任务。')
    rows=[]
    for i,(video,content,profile) in enumerate(zip(videos,texts,profiles),1):
        if not video.stat().st_size or len(content)>25000:
            raise ValueError(f'第 {i} 项视频为空或文案过长。')
        rows.append(dict(order=i,profile_id=profile['id'],profile_name=profile.get('name') or str(profile['seq']),seq=profile['seq'],text=content,video=str(video.resolve()),size=video.stat().st_size,modified=video.stat().st_mtime_ns))
    signature=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {'rows':rows,'signature':signature,'failure_folder':str(store.DATA/'failed')}


def apply_edits(prepared, edits, available):
    if edits is None:
        return prepared['rows']
    originals = {row['order']: row for row in prepared['rows']}
    profiles = {profile['id']: profile for profile in available}
    if not isinstance(edits, list) or not 1 <= len(edits) <= len(originals):
        raise ValueError('至少保留一条任务。')
    rows, used_orders, used_profiles = [], set(), set()
    for edit in edits:
        order = edit.get('order') if isinstance(edit, dict) else None
        if type(order) is not int or order not in originals or order in used_orders:
            raise ValueError('配对行无效，请重新预览。')
        pid, content = edit.get('profile_id'), edit.get('text')
        if not isinstance(pid, str) or pid not in profiles or pid in used_profiles:
            raise ValueError('每行须选择一个有效且不重复的窗口。')
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 25000:
            raise ValueError(f'第 {order} 行文案为空或超过 25,000 字符。')
        used_orders.add(order)
        used_profiles.add(pid)
        profile = profiles[pid]
        rows.append(dict(originals[order], profile_id=pid,
                         profile_name=profile.get('name') or str(profile['seq']), text=content.strip()))
    return rows


def create(rows):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return [store.create_task(row,db=db) for row in rows]
