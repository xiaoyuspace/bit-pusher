import hashlib
import json
import re
from pathlib import Path
import store


def natural_key(path):
    return [int(s) if s.isdigit() else s.casefold() for s in re.split(r'(\d+)', path.name)]


def plan(folder, text_file, selected, available):
    root = Path(str(folder).strip().strip('"')).expanduser()
    text = Path(str(text_file).strip().strip('"')).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('请选择存在的视频文件夹完整路径。')
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


def create(plan_data):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return [store.create_task(row,db=db) for row in plan_data['rows']]
