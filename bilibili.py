"""Bilibili Creator Center video submissions, one queue item per video."""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import store
from batch import natural_key


UPLOAD_URL = 'https://member.bilibili.com/platform/upload/video/frame'
HOME_URL = 'https://www.bilibili.com/'


def plan(body, profiles):
    profile = next((w for w in profiles if w['id'] == body.get('profile')), None)
    if not profile:
        raise ValueError('请选择投稿窗口。')
    root = Path(body.get('folder') or '')
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('请选择本地视频文件夹。')
    tags = list(dict.fromkeys(t.strip() for t in re.split('[,，]', body.get('tags') or '') if t.strip()))
    if not 1 <= len(tags) <= 10 or any(len(t) > 20 for t in tags):
        raise ValueError('请填写 1–10 个标签，每个不超过 20 字。')
    files = sorted((p for p in root.iterdir() if p.is_file() and p.suffix.lower() in
                    ('.mp4', '.mov', '.webm')), key=lambda p: (natural_key(p), p.name))
    if not 1 <= len(files) <= 200:
        raise ValueError('请选择含有 1–200 个 MP4、MOV 或 WebM 文件的文件夹。')
    rows = []
    for path in files:
        stat = path.stat()
        if not stat.st_size:
            raise ValueError(f'{path.name} 为空文件。')
        rows.append(dict(video=str(path.resolve()), filename=path.name, text=path.stem[:80],
                         profile_id=profile['id'], profile_name=profile.get('name') or str(profile['seq']),
                         size=stat.st_size, modified=stat.st_mtime_ns, tags=','.join(tags)))
    signature = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return rows, signature


def is_submission(response):
    url = urlsplit(response.url)
    return (response.request.method == 'POST' and url.hostname == 'member.bilibili.com'
            and re.fullmatch(r'/x/(?:(?:v\d+|vu|vupre)/)?web/(?:(?:archives?|article)/)?add(?:/v\d+)?/?', url.path) is not None)


def publish(publisher, task):
    from playwright.sync_api import sync_playwright
    from window_manager import browser_debug_endpoint, close_window_tabs
    from worker import bit

    tid, pid = task['id'], task['profile_id']
    submitted = False
    try:
        publisher.wait_for_previous_close(pid)
        publisher.checkpoint()
        path, digest = store.file_hash(task['video'])
        if digest != task['file_hash']:
            raise ValueError('素材已变化，请重新创建任务。')
        with sync_playwright() as pw:
            for attempt in range(3):
                endpoint = browser_debug_endpoint(bit('/browser/open', {'id': pid}))
                try:
                    browser = pw.chromium.connect_over_cdp(endpoint, is_local=True, timeout=30000)
                    page = browser.contexts[0].new_page()
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2)
            page.set_default_timeout(15000)
            page.goto(UPLOAD_URL, wait_until='domcontentloaded', timeout=60000)
            page.locator('input[name=buploader]').first.wait_for(state='attached')
            upload = page.locator('#video-up-app input[type=file][accept*=".mp4"]').first
            upload.wait_for(state='attached')
            if page.get_by_text('继续编辑', exact=True).first.is_visible():
                raise ValueError('该窗口有 B 站未提交的本地草稿，请先处理后重试。')
            store.update(tid, 'uploading', '正在上传至 B 站创作中心。')
            source = Path(path)
            file_input = path
            if source.stat().st_size <= 8 * 1024 * 1024:
                file_input = {'name': source.name, 'mimeType': 'video/' + source.suffix[1:],
                              'buffer': source.read_bytes()}
            upload.set_input_files(file_input, timeout=120000)
            title = page.locator('input[placeholder*="标题"]:visible, .video-title input:visible').first
            deadline = time.monotonic() + 1200
            form_deadline = time.monotonic() + 90
            while not title.is_visible():
                publisher.checkpoint()
                if time.monotonic() >= form_deadline or 'passport.bilibili.com' in page.url:
                    raise ValueError('未进入投稿编辑页，请检查 B 站登录和上传状态。')
                page.wait_for_timeout(500)
            if not page.get_by_text(source.stem, exact=True).first.is_visible() and title.input_value() != source.stem:
                raise ValueError('当前编辑页的视频名称与任务素材不一致，请手动检查。')
            title.fill(task['text'])
            tag_input = page.locator('input[placeholder*="标签"]:visible, .tag-input-wrp input:visible').first
            for tag in task['bili_tags'].split(','):
                publisher.checkpoint()
                tag_input.fill(tag)
                tag_input.press('Enter')
            uploaded = page.get_by_text(re.compile(r'上传完成|上传成功'))
            while not uploaded.first.is_visible():
                publisher.checkpoint()
                if time.monotonic() >= deadline or page.get_by_text('上传失败', exact=True).first.is_visible():
                    raise ValueError('视频上传未完成，请在窗口中检查。')
                page.wait_for_timeout(750)
            if title.input_value().strip() != task['text']:
                raise ValueError('投稿标题未保存，请检查。')
            submit = page.get_by_text('立即投稿', exact=True).first
            submit.click(trial=True)
            publisher.checkpoint()
            store.update(tid, 'submitting', '已准备提交 B 站稿件。')
            submitted = True
            with page.expect_response(is_submission, timeout=60000) as received:
                submit.click()
            payload = received.value.json()
            if payload.get('code') != 0:
                if isinstance(payload.get('code'), int):
                    submitted = False
                raise ValueError('B 站返回：' + str(payload.get('message') or '投稿未确认')[:150])
            data = payload.get('data') or {}
            bvid = str(data.get('bvid') or '')
            aid = str(data.get('aid') or '')
            if re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid):
                video_id = bvid
            elif aid.isdigit() and int(aid) > 0:
                video_id = 'av' + aid
            else:
                raise ValueError('未收到稿件编号。')
            url = 'https://www.bilibili.com/video/' + video_id
            store.update(tid, 'success', 'B 站已接收稿件，等待平台审核。', url)
        try:
            close_window_tabs(pid, keep_url=HOME_URL)
            bit('/browser/close', {'id': pid})
        except Exception:
            store.update(tid, 'success', '投稿成功，标签页整理或关窗未完成。', url)
    except InterruptedError:
        store.update(tid, 'unknown' if submitted else 'cancelled', '任务已停止，请检查稿件状态。')
    except Exception as exc:
        reason = str(exc)[:180] if isinstance(exc, ValueError) else '创作中心页面、网络或必填项未就绪。'
        store.update(tid, 'unknown' if submitted else 'failed',
                     reason + (' 请到稿件管理核实，避免重复投稿。' if submitted else ' 本次未成功投稿。'))
