"""Local profile lifecycle. Never persist tokens in the publisher database."""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
import threading
from datetime import datetime
from urllib.parse import urlparse
import store

operation_lock = threading.Lock()
managed_profiles = None
job = {'running': False, 'results': []}
DETECTION_WORKERS = 3

def init():
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS windows(id TEXT PRIMARY KEY,state TEXT NOT NULL,reason TEXT,checked REAL)")

        db.execute("CREATE TABLE IF NOT EXISTS window_proxies(id TEXT PRIMARY KEY,line TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS window_accounts(id TEXT PRIMARY KEY, username TEXT, password TEXT, twofa TEXT)")


def save_account(pid, account):
    if account:
        with store.connect() as db:
            db.execute('INSERT OR REPLACE INTO window_accounts VALUES (?,?,?,?)', (pid, *account))


TOKEN_PATTERN = r'[a-zA-Z0-9_-]{20,512}'
ACCOUNT_PATTERN = re.compile(r'([A-Za-z0-9_]{1,15})-(.+)-([A-Za-z2-7]{16,64})-(' + TOKEN_PATTERN + r')')


def parse_login(line):
    match = ACCOUNT_PATTERN.fullmatch(line)
    if match:
        return match.groups()[:3], match[4]
    if re.fullmatch(TOKEN_PATTERN, line):
        return None, line
    raise ValueError('登录信息格式应为 账号-密码-2FA-token。')


def extract_logins(text):
    results = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = re.split(r'——|----', line.strip(), maxsplit=3)
        tail = re.split(r'——|----', fields[-1])
        if len(fields) == 4 and len(tail) in (2, 3):
            username, password = fields[:2]
            twofa, token = tail[-2:]
            compact = '-'.join((username, password, twofa, token))
            if ACCOUNT_PATTERN.fullmatch(compact):
                results.append(compact)
                continue
        raise ValueError(f'第 {number} 行卡密格式不正确。')
    if not results:
        raise ValueError('请先粘贴或选择卡密 TXT。')
    return '\n'.join(results)


def save_proxy(pid, line):
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO window_proxies VALUES (?,?)', (pid, line))


def export_credentials():
    from worker import profiles

    ids = {profile['id'] for profile in profiles()}
    init()
    with store.connect() as db:
        db.execute('BEGIN')
        proxies = dict(db.execute('SELECT id,line FROM window_proxies'))
        accounts = {row[0]: list(row[1:]) for row in db.execute(
            'SELECT id,username,password,twofa FROM window_accounts')}
    return {pid: {'proxy': proxies.get(pid), 'account': accounts.get(pid)}
            for pid in sorted(ids & (proxies.keys() | accounts.keys()))}


def import_credentials(records):
    from worker import profiles

    if not isinstance(records, dict) or not records:
        raise ValueError('凭据文件没有可导入的窗口。')
    for pid, record in records.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', pid) or not isinstance(record, dict):
            raise ValueError('凭据文件格式无效。')
        line, account = record.get('proxy'), record.get('account')
        if line is not None:
            if not isinstance(line, str) or len(line) > 2048:
                raise ValueError('凭据文件中的代理记录无效。')
            proxy(line)
        if account is not None and (
            not isinstance(account, list) or len(account) != 3
            or any(not isinstance(value, str) or not value or len(value) > 2048 for value in account)
            or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', account[0])
            or not re.fullmatch(r'[A-Za-z2-7]{16,64}', account[2])
        ):
            raise ValueError('凭据文件中的账号记录无效。')

    available = {profile['id'] for profile in profiles()}
    counts = dict(imported=0, existing=0, missing=0)
    init()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for pid, record in records.items():
            if pid not in available:
                counts['missing'] += 1
                continue
            inserted = False
            if record.get('proxy'):
                inserted |= bool(db.execute('INSERT OR IGNORE INTO window_proxies VALUES (?,?)',
                                            (pid, record['proxy'])).rowcount)
            if record.get('account'):
                inserted |= bool(db.execute('INSERT OR IGNORE INTO window_accounts VALUES (?,?,?,?)',
                                            (pid, *record['account'])).rowcount)
            counts['imported' if inserted else 'existing'] += 1
    return counts


def state(pid):
    init()
    with store.connect() as db:
        row = db.execute('SELECT * FROM windows WHERE id=?',(pid,)).fetchone()
    return dict(row) if row else dict(id=pid,state='pending',reason='尚未检测，请配置或检测现有窗口。',checked=None)

def mark(pid, status, reason):
    init()
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO windows VALUES(?,?,?,?)',(pid,status,reason,time.time()))

def listing():
    from worker import profiles
    init()
    with store.connect() as db:
        saved = dict(db.execute('SELECT id,line FROM window_proxies'))
        accounts = {row['id']: dict(row) for row in db.execute(
            'SELECT id, username, password AS account_password, twofa FROM window_accounts')}
    windows = []
    for profile in profiles():
        line = saved.get(profile['id'])
        if line:
            host, _, username, password, _ = line.split('|')
            profile.update(proxy_ip=host, proxy_user=username, proxy_password=password)
        windows.append({**profile, **accounts.get(profile['id'], {}), **state(profile['id'])})
    return windows

def require_ready(ids):
    if not ids or any(state(i)['state'] != 'ready' for i in ids):
        raise ValueError('只能选择可使用窗口；请先在窗口管理中配置并检测。')

def proxy(value):
    parts = value.strip().split('|')
    if len(parts)!=5 or not parts[0].strip() or not parts[1].isdigit() or not 1<=int(parts[1])<=65535:
        raise ValueError('代理格式应为 主机|端口|用户名|密码|创建时间。')
    try:
        datetime.strptime(parts[4], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        raise ValueError('代理创建时间格式应为 YYYY-MM-DD HH:MM:SS。') from None
    return dict(proxyMethod=2,proxyType='socks5',host=parts[0].strip(),port=int(parts[1]),proxyUserName=parts[2],proxyPassword=parts[3])

class PostCheckIncomplete(ValueError):
    pass


def check_own_post(page):
    from worker import check_access, handle_known_prompts, AccountUnavailable
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page.goto('https://x.com/home', wait_until='domcontentloaded', timeout=45000)
    check_access(page)
    handle_known_prompts(page)
    profile_path = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        check_access(page)
        handle_known_prompts(page)
        candidate = page.evaluate("""() => {
            const link = document.querySelector('[data-testid="AppTabBar_Profile_Link"]');
            const switcher = document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]');
            const prefix = 'UserAvatar-Container-';
            const avatar = switcher?.querySelector('[data-testid^="' + prefix + '"]');
            return link?.getAttribute('href') ||
                   (avatar ? '/' + avatar.getAttribute('data-testid').slice(prefix.length) : '');
        }""")
        if candidate and re.fullmatch(r'/[A-Za-z0-9_]{1,15}', candidate):
            profile_path = candidate
            break
        page.wait_for_timeout(500)
    if not profile_path:
        raise PostCheckIncomplete('未能识别当前登录账号，暂无法确认冻结状态，请重试检测。')

    page.goto('https://x.com' + profile_path, wait_until='domcontentloaded', timeout=45000)
    check_access(page)
    primary = page.get_by_test_id('primaryColumn')
    handle_known_prompts(page)
    own_posts = primary.get_by_role('article').locator(f'a[href^="{profile_path}/status/" i]')
    try:
        own_posts.first.wait_for(state='visible', timeout=15000)
    except PlaywrightTimeout:
        check_access(page)
        raise PostCheckIncomplete('个人主页未找到本账号的作品，暂无法确认冻结状态。') from None

    paths = own_posts.evaluate_all("(links) => links.map(link => new URL(link.href).pathname)")
    post_path = next((path for path in paths if re.fullmatch(
        re.escape(profile_path) + r'/status/[0-9]+', path, re.I
    )), None)
    if not post_path:
        raise PostCheckIncomplete('未找到本账号的作品详情链接，暂无法确认冻结状态。')

    page.goto('https://x.com' + post_path, wait_until='domcontentloaded', timeout=45000)
    target_post = primary.get_by_role('article').filter(
        has=page.locator(f'a[href="{post_path}" i] time')
    )
    first_cell = primary.get_by_test_id('cellInnerDiv').first
    suspension = first_cell.get_by_text(re.compile(
        r'^This Post is from a suspended account\.?(?:\s+Learn more)?$', re.I
    ))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        check_access(page)
        if urlparse(page.url).path.lower() != post_path.lower():
            break
        handle_known_prompts(page)
        if target_post.first.is_visible():
            return
        if suspension.first.is_visible():
            raise AccountUnavailable('账号已被冻结：打开本账号作品后，X 提示此作品来自已冻结的账号。')
        page.wait_for_timeout(500)
    raise PostCheckIncomplete('作品详情未正常加载，暂无法确认冻结状态，请重试检测。')


def token_cookie_patch(token, detail):
    cookies = detail.get('cookie') or []
    if isinstance(cookies, str):
        cookies = json.loads(cookies)
    if not isinstance(cookies, list) or any(not isinstance(c, dict) for c in cookies):
        raise ValueError('窗口 Cookie 格式异常，未修改。')
    cookies = [c for c in cookies if not (
        c.get('name') == 'auth_token' and str(c.get('domain', '')).lstrip('.').lower() == 'x.com'
    )]
    cookies.append(dict(name='auth_token',value=token,domain='.x.com',path='/',secure=True,httpOnly=True,expirationDate=time.time()+86400*365))
    return {
        'cookie': json.dumps(cookies),
        'clearCookiesBeforeLaunch': False,
    }


def save_token(pid, token, detail):
    from worker import bit

    # Verify login after loading the saved profile, as on a later reopen.
    bit('/browser/update/partial', {
        'ids': [pid],
        'browserFingerPrint': {},
        **token_cookie_patch(token, detail),
    })


def browser_debug_endpoint(opened):
    endpoint = opened.get('ws') or 'http://' + opened.get('http', '')
    parsed = urlparse(endpoint)
    if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.scheme not in ('http', 'ws'):
        raise ValueError('无效本机调试地址')
    return endpoint


def close_window_tabs(pid):
    from playwright.sync_api import sync_playwright
    from worker import bit

    with sync_playwright() as pw:
        endpoint = browser_debug_endpoint(bit('/browser/open', {'id': pid}))
        browser = pw.chromium.connect_over_cdp(endpoint, is_local=True, timeout=30000)
        old_pages = [page for context in browser.contexts for page in context.pages]
        # Keep Chromium open until BitBrowser can close and save the empty session.
        browser.contexts[0].new_page()
        for page in old_pages:
            page.close()


def clear_window_data(pid):
    from worker import bit

    deadline = time.monotonic() + 15
    while bit('/browser/pids/alive', {'ids': [pid]}).get(pid):
        if time.monotonic() >= deadline:
            raise TimeoutError('窗口未关闭')
        time.sleep(0.25)
    bit('/cache/clear', {'ids': [pid]})
    bit('/browser/cookies/clear', {'browserId': pid, 'saveSynced': False})


def inspect_window(pid, token=''):
    from worker import bit,check_access,handle_known_prompts,AccountUnavailable
    detail = bit('/browser/detail',{'id':pid})
    if token:
        save_token(pid, token, detail)
    if not detail.get('host') or not detail.get('port') or detail.get('proxyType')!='socks5':
        mark(pid,'pending','token 已保存至比特窗口，请配置 SOCKS5 代理后检测登录。' if token else '请先配置 SOCKS5 代理。')
        return
    from playwright.sync_api import sync_playwright

    page = None
    try:
        with sync_playwright() as pw:
            opened=bit('/browser/open',{'id':pid})
            endpoint=browser_debug_endpoint(opened)
            browser=pw.chromium.connect_over_cdp(endpoint,is_local=True,timeout=30000)
            context=browser.contexts[0]
            page=context.new_page()
            try:
                # The composer mask intercepts Cookie banner clicks on fresh profiles.
                check_own_post(page)
                try:
                    page.goto('https://x.com/compose/post',wait_until='domcontentloaded',timeout=60000)
                except Exception:
                    check_access(page)
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    check_access(page)
                    if '/i/flow/login' in page.url or '/login' == urlparse(page.url).path:
                        mark(pid,'pending','X 登录已失效，请重新配置 token。')
                        return
                    handle_known_prompts(page)
                    editor=page.get_by_test_id('tweetTextarea_0')
                    if editor.count() and editor.first.is_visible():
                        break
                    page.wait_for_timeout(750)
                else:
                    raise TimeoutError()
            finally:
                if page and not page.is_closed():
                    page.close()
    except AccountUnavailable as e:
        mark(pid,'delete',str(e))
    except PostCheckIncomplete as e:
        mark(pid,'pending',str(e))
    except Exception:
        # Inconclusive checks cannot certify eligibility, and never imply suspension.
        mark(pid,'pending','检测未完成：网络、页面或登录异常；请重试检测。')
    else:
        message = '检测通过：已登录，发帖框与本账号作品可正常打开。'
        try:
            bit('/browser/close', {'id': pid})
        except Exception:
            message += ' 窗口未能自动关闭，可手动关闭。'
        else:
            message += ' 比特窗口已关闭。'
        mark(pid, 'ready', message)

def prepare(body):
    from worker import profiles
    action=body.get('action')
    if action not in ('create','configure','edit','detect','delete'):
        raise ValueError('无效窗口操作。')
    available={r['id']:r for r in profiles()}
    ids=body.get('ids',[])
    if not isinstance(ids,list) or len(set(ids))!=len(ids) or any(i not in available for i in ids):
        raise ValueError('窗口选择无效，请刷新。')
    ids=sorted(ids,key=lambda i:int(available[i].get('seq') or 0))
    names=[s.strip() for s in str(body.get('names','')).splitlines() if s.strip()]
    n=len(names) if action=='create' else len(ids)
    if not 1<=n<=200:
        raise ValueError('请选择 1–200 个窗口或每行填写一个新窗口名。')
    if action=='edit' and n!=1:
        raise ValueError('修改备注和代理时请选择一个窗口。')
    if any(len(s)>100 for s in names):
        raise ValueError('窗口名最长 100 字。')
    proxies=[s.strip() for s in str(body.get('proxies','')).splitlines() if s.strip()]
    tokens=[s.strip() for s in str(body.get('tokens','')).splitlines() if s.strip()]
    for values in (proxies,tokens):
        if values and len(values)!=n:
            raise ValueError('代理和 token 须各按行与窗口数量一致；不修改的整项可留空。')
    patches=[(s, proxy(s)) for s in proxies]
    tokens = [parse_login(line) for line in tokens]
    if action=='delete' and any(state(i)['state']!='delete' for i in ids):
        raise ValueError('只能删除待删除状态的窗口。')
    if action=='delete' and body.get('confirm') is not True:
        raise ValueError('请确认删除选中的窗口。')
    return action,ids,names,patches,tokens,str(body.get('remark',''))[:2000]

def require_unmanaged(ids):
    # A lock without a profile set belongs to a create or credential import.
    reserved = managed_profiles
    if operation_lock.locked() and (reserved is None or reserved.intersection(ids)):
        raise ValueError('所选窗口正在管理，请稍后再发布。')


def release_management():
    global managed_profiles
    managed_profiles = None
    operation_lock.release()


def manual_check(body):
    global managed_profiles
    from worker import bit, profiles, publisher

    pid = body.get('id')
    action = body.get('action')
    if action not in ('open', 'confirm'):
        raise ValueError('无效的手动检测操作。')
    labels = {'pending': '待配置', 'ready': '可使用', 'delete': '待删除'}
    results = body.get('results', [{'id': pid, 'state': body.get('state')}])
    if action == 'open':
        results = [{'id': pid}]
    elif body.get('confirmed') is not True:
        raise ValueError('请确认手动检测结果。')
    if not isinstance(results, list) or not 1 <= len(results) <= 200:
        raise ValueError('请选择 1–200 个窗口。')
    ids = set()
    for row in results:
        target = row.get('id') if isinstance(row, dict) else None
        if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', target) or target in ids:
            raise ValueError('请选择有效且不重复的窗口。')
        if action == 'confirm' and (not isinstance(row.get('state'), str) or row['state'] not in labels):
            raise ValueError('请选择有效的窗口状态。')
        ids.add(target)

    with publisher.lock:
        if ids.intersection(publisher.reserved_profiles()):
            raise ValueError('所选窗口中有窗口已被发布队列占用。')
        if not operation_lock.acquire(blocking=False):
            raise ValueError('窗口操作正在执行，请稍候。')
        managed_profiles = ids
    try:
        if action == 'open':
            bit('/browser/open', {'id': pid})
            return
        if not ids.issubset(profile['id'] for profile in profiles()):
            raise ValueError('有窗口已不存在，请刷新列表。')
        init()
        checked = time.time()
        with store.connect() as db:
            db.executemany('INSERT OR REPLACE INTO windows VALUES(?,?,?,?)', [
                (row['id'], row['state'], f'人工检测：已手动标为「{labels[row["state"]]}」。', checked)
                for row in results
            ])
    finally:
        release_management()


def start(body):
    global managed_profiles
    from worker import publisher

    args = prepare(body)
    action, ids, *_ = args
    with publisher.lock:
        if action != 'create' and publisher.reserved_profiles().intersection(ids):
            raise ValueError('所选窗口中有窗口已被发布队列占用，请取消勾选后重试。')
        if not operation_lock.acquire(blocking=False):
            raise ValueError('窗口操作正在执行，请稍候。')
        try:
            managed_profiles = None if action == 'create' else set(ids)
            workers = min(DETECTION_WORKERS, len(ids)) if action == 'detect' else 1
            job.update(running=True, results=[], action=action, total=len(ids), workers=workers,
                       run_id=str(time.time_ns()), deleted_proxies=[])
            threading.Thread(target=perform,args=args,daemon=True).start()
        except Exception:
            release_management()
            raise

def detect_windows(ids, tokens):
    with ThreadPoolExecutor(max_workers=min(DETECTION_WORKERS, len(ids))) as pool:
        pending = {
            pool.submit(inspect_window, pid, tokens[index][1] if tokens else ''): pid
            for index, pid in enumerate(ids)
        }
        for future in as_completed(pending):
            pid = pending[future]
            try:
                future.result()
                message = state(pid)['reason']
            except Exception:
                message = '检测失败，请检查比特浏览器或重试。'
            job['results'].append({'id': pid, 'name': pid, 'message': message})


def perform(action,ids,names,patches,tokens,remark):
    from worker import bit
    try:
        if action == 'detect':
            detect_windows(ids, tokens)
            return
        for index in range(len(names) if action=='create' else len(ids)):
            pid=None if action=='create' else ids[index]
            account, token = tokens[index] if tokens else (None, '')
            failure = '操作失败，请检查比特浏览器、窗口状态和输入；未输出凭据。'
            try:
                if action=='create':
                    result=bit('/browser/update',dict(name=names[index],platform='https://x.com',platformIcon='x.com',url='https://x.com/home',remark=remark,browserFingerPrint={},**(patches[index][1] if patches else {})))
                    pid=result['id']
                    mark(pid, 'ready', '窗口已创建，默认可使用。')
                    if patches:
                        save_proxy(pid, patches[index][0])
                    if token:
                        save_token(pid, token, bit('/browser/detail', {'id': pid}))
                        save_account(pid, account)
                elif action=='delete':
                    with store.connect() as db:
                        saved = db.execute('SELECT line FROM window_proxies WHERE id=?', (pid,)).fetchone()
                    if saved:
                        line = saved['line']
                    else:
                        detail = bit('/browser/detail', {'id': pid})
                        # BitBrowser may return an encoded password for an existing profile.
                        line = '|'.join(str(detail.get(field) or '') for field in
                                        ('host', 'port', 'proxyUserName')) + '||'
                        if not detail.get('host') or detail.get('proxyType') == 'noproxy':
                            line = ''
                    if bit('/browser/pids/alive',{'ids':[pid]}).get(pid):
                        bit('/browser/close',{'id':pid})
                    bit('/browser/delete',{'id':pid})
                    if line:
                        job['deleted_proxies'].append(line)
                    with store.connect() as db:
                        db.execute('DELETE FROM window_proxies WHERE id=?', (pid,))
                        db.execute('DELETE FROM window_accounts WHERE id=?', (pid,))
                        db.execute('DELETE FROM windows WHERE id=?',(pid,))
                elif action in ('configure','edit'):
                    replace_account = action == 'configure' and bool(patches and token)
                    if patches or tokens:
                        mark(pid,'pending','配置已开始，等待重新检测。')
                        # Close before saving so a running session cannot overwrite the new settings.
                        if bit('/browser/pids/alive',{'ids':[pid]}).get(pid):
                            if replace_account:
                                failure = '关闭旧标签页失败，请关闭该比特窗口后重新提交代理和登录信息。'
                                close_window_tabs(pid)
                                failure = '关闭比特窗口失败，请关闭后重新提交代理和登录信息。'
                            bit('/browser/close',{'id':pid})
                    patch={'ids':[pid],'browserFingerPrint':{}}
                    if patches:
                        patch.update(patches[index][1])
                    if action=='edit':
                        patch['remark']=remark
                    if replace_account:
                        failure = '清空窗口数据失败。请确认窗口已关闭、比特浏览器支持清理接口，再重新提交代理和登录信息。'
                        clear_window_data(pid)
                        patch.update(token_cookie_patch(token, {}))
                        # Synced tabs and configured startup URLs can restore old pages.
                        patch.update(syncTabs=False, url='https://x.com/home')
                        failure = '旧窗口数据已清空，但保存新配置未完成。请重新提交代理和登录信息。'
                    bit('/browser/update/partial',patch)
                    if replace_account:
                        with store.connect() as db:
                            db.execute('INSERT OR REPLACE INTO window_proxies VALUES (?,?)', (pid, patches[index][0]))
                            if account:
                                db.execute('INSERT OR REPLACE INTO window_accounts VALUES (?,?,?,?)', (pid, *account))
                            else:
                                db.execute('DELETE FROM window_accounts WHERE id=?', (pid,))
                        failure = '新代理和登录信息已保存，但检测未完成，请重新检测。'
                    elif patches:
                        save_proxy(pid, patches[index][0])
                    if action == 'configure' or patches:
                        inspect_window(pid, '' if replace_account else token)
                        if not replace_account:
                            save_account(pid, account)
                current=state(pid) if action!='delete' else {'reason':'已删除'}
                job['results'].append({'id':pid,'name':names[index] if action=='create' else pid,'message':current['reason']})
            except Exception:
                job['results'].append({'id':pid,'message':failure})
    finally:
        tokens.clear()
        patches.clear()
        job['running']=False
        release_management()
