"""Bilibili Creator Center video submissions, one queue item per video."""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import store
from batch import natural_key, read_txt


UPLOAD_URL = 'https://member.bilibili.com/platform/upload/video/frame'
HOME_URL = 'https://www.bilibili.com/'


def checked_title(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or len(value.strip().splitlines()) != 1:
        raise ValueError('每条标题须为一行，长度为 1–80 字。')
    return value.strip()


def batch_options(body):
    options = {}
    for key, label, limit in [('description', '简介', 2000), ('statement', '创作声明', 80),
                              ('category', '分区', 100), ('source', '转载来源', 200)]:
        value = body.get(key, '')
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise ValueError(f'{label}最多可填 {limit} 字。')
        options[key] = value.strip()
    if not options['statement'] or not options['category']:
        raise ValueError('请选择创作声明并填写分区。')
    if options['statement'] == '内容为转载' and not options['source']:
        raise ValueError('请填写整批转载视频的实际来源。')
    return options


def plan(body, profiles):
    ids = body.get('profiles')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or any(not isinstance(i, str) for i in ids):
        raise ValueError('请勾选 1–200 个投稿窗口。')
    selected = set(ids)
    targets = sorted((w for w in profiles if w['id'] in selected), key=lambda w: int(w['seq']))
    if len(selected) != len(ids) or len(targets) != len(ids):
        raise ValueError('窗口重复或已不存在，请刷新后重新选择。')
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
    if len(files) != len(targets):
        raise ValueError(f'已选 {len(targets)} 个窗口，文件夹有 {len(files)} 个视频。每个窗口需要一个视频，请调整数量。')
    options = batch_options(body)
    if body.get('title_file'):
        path = Path(body['title_file'])
        if not path.is_absolute() or path.suffix.lower() != '.txt' or not path.is_file() or path.stat().st_size > 5_000_000:
            raise ValueError('请选择不超过 5 MB 的本地标题 TXT 文件。')
        titles = re.split(r'(?m)^[^\S\n]*===[^\S\n]*$', read_txt(path))
        if len(titles) != len(files):
            raise ValueError(f'共有 {len(titles)} 条标题、{len(files)} 个视频，请用单独一行 === 分隔每条标题。')
        for i, title in enumerate(titles, 1):
            try:
                titles[i - 1] = checked_title(title)
            except ValueError as exc:
                raise ValueError(f'标题文件第 {i} 条有误。{exc}') from exc
    else:
        titles = [p.stem[:80] for p in files]
    rows = []
    for path, profile, title in zip(files, targets, titles):
        stat = path.stat()
        if not stat.st_size:
            raise ValueError(f'{path.name} 为空文件。')
        rows.append(dict(video=str(path.resolve()), filename=path.name, text=title, bili_options=options,
                         seq=profile['seq'], profile_id=profile['id'], profile_name=profile.get('name') or str(profile['seq']),
                         size=stat.st_size, modified=stat.st_mtime_ns, tags=','.join(tags)))
    signature = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return rows, signature


def is_submission(response):
    url = urlsplit(response.url)
    return (response.request.method == 'POST' and url.hostname == 'member.bilibili.com'
            and re.fullmatch(r'/x/(?:(?:v\d+|vu|vupre)/)?web/(?:(?:archives?|article)/)?add(?:/v\d+)?/?', url.path) is not None)


def fill_submission_fields(page, task):
    from playwright.sync_api import Error, expect

    options = json.loads(task['bili_options'])
    field = '标题'
    try:
        title = page.locator('input[placeholder*="标题"]:visible, .video-title input:visible').first
        title.fill(task['text'])
        if options:
            field = '创作声明'
            statement = page.locator('.creation-statement-container:visible')
            selected = statement.locator('.bcc-select-input-inner')
            if selected.input_value() != options['statement']:
                selected.click()
                statement.locator('.bcc-option:not(.is-disabled)').get_by_text(options['statement'], exact=True).click()
            expect(selected).to_have_value(options['statement'])
            if options['statement'] == '内容为转载':
                field = '转载来源'
                source = statement.locator('.statement-source input')
                source.fill(options['source'])
                expect(source).to_have_value(options['source'])

            field = '分区'
            category = page.locator('.video-human-type:visible, .video-type:visible').first
            category_name = ' → '.join(part.strip() for part in options['category'].split('→'))
            current = category.locator('.select-item-cont')
            if current.inner_text().strip() != category_name:
                category.locator('.select-controller').click()
                if ' → ' in category_name:
                    parent, child = category_name.split(' → ', 1)
                    category.locator('.drop-f-wrp').get_by_text(parent, exact=True).click()
                    category.locator('.drop-t-wrp').get_by_text(child, exact=True).click()
                else:
                    category.locator('.human-type-list').get_by_text(category_name, exact=True).click()
            expect(current).to_have_text(category_name)

            field = '简介'
            editor = page.locator('.desc-container:visible .ql-editor[contenteditable=true]')
            editor.fill(options['description'])
            editor.press('Tab')
            expect(editor).to_have_text(options['description'], use_inner_text=True)

        field = '标签'
        tags = task['bili_tags'].split(',')
        group = page.locator('.tag-input-wrp:visible').filter(has=page.locator('input[placeholder*="标签"]'))
        labels = group.locator('.label-item-v2-content')
        for existing in labels.all_text_contents():
            if existing not in tags:
                group.locator('.label-item-v2-container').filter(has_text=re.compile('^' + re.escape(existing) + '$')).click()
                expect(group.get_by_text(existing, exact=True)).to_have_count(0)
        for tag in tags:
            if tag not in labels.all_text_contents():
                tag_input = group.locator('input')
                tag_input.fill(tag)
                tag_input.press('Enter')
                expect(group.get_by_text(tag, exact=True)).to_be_visible()
        if sorted(labels.all_text_contents()) != sorted(tags):
            raise ValueError('页面标签与整批配置不一致，请检查该窗口。')
        field = '标题'
        expect(title).to_have_value(task['text'])
    except (Error, AssertionError) as exc:
        raise ValueError(f'无法填写或核实「{field}」，请检查该窗口的投稿页和整批配置。') from exc


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
            uploaded = page.get_by_text(re.compile(r'上传完成|上传成功'))
            while not uploaded.first.is_visible():
                publisher.checkpoint()
                if time.monotonic() >= deadline or page.get_by_text('上传失败', exact=True).first.is_visible():
                    raise ValueError('视频上传未完成，请在窗口中检查。')
                page.wait_for_timeout(750)
            publisher.checkpoint()
            fill_submission_fields(page, task)
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
