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
job = {'running': False, 'results': []}
DETECTION_WORKERS = 3

def init():
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS windows(id TEXT PRIMARY KEY,state TEXT NOT NULL,reason TEXT,checked REAL)")

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
    return [{**r,**state(r['id'])} for r in profiles()]

def require_ready(ids):
    if not ids or any(state(i)['state'] != 'ready' for i in ids):
        raise ValueError('只能选择可使用窗口；请先在窗口管理中配置并检测。')

def proxy(value):
    parts = value.strip().split('|')
    if len(parts)!=5 or not parts[0].strip() or not parts[1].isdigit() or not 1<=int(parts[1])<=65535:
        raise ValueError('代理格式应为 主机|端口|用户名|密码|到期时间。')
    try:
        datetime.strptime(parts[4], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        raise ValueError('代理到期时间格式应为 YYYY-MM-DD HH:MM:SS。') from None
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


def inspect_window(pid, token=''):
    from worker import bit,check_access,handle_known_prompts,AccountUnavailable
    detail = bit('/browser/detail',{'id':pid})
    if token:
        # Verify login after loading the saved profile, as on a later reopen.
        cookies = detail.get('cookie') or []
        if isinstance(cookies, str):
            cookies = json.loads(cookies)
        if not isinstance(cookies, list) or any(not isinstance(c, dict) for c in cookies):
            raise ValueError('窗口 Cookie 格式异常，未修改。')
        cookies = [c for c in cookies if not (
            c.get('name') == 'auth_token' and str(c.get('domain', '')).lstrip('.').lower() == 'x.com'
        )]
        cookies.append(dict(name='auth_token',value=token,domain='.x.com',path='/',secure=True,httpOnly=True,expirationDate=time.time()+86400*365))
        bit('/browser/update/partial', {
            'ids': [pid],
            'browserFingerPrint': {},
            'cookie': json.dumps(cookies),
            'clearCookiesBeforeLaunch': False,
        })
    if not detail.get('host') or not detail.get('port') or detail.get('proxyType')!='socks5':
        mark(pid,'pending','token 已保存至比特窗口，请配置 SOCKS5 代理后检测登录。' if token else '请先配置 SOCKS5 代理。')
        return
    from playwright.sync_api import sync_playwright

    page = None
    try:
        with sync_playwright() as pw:
            opened=bit('/browser/open',{'id':pid})
            endpoint=opened.get('ws') or 'http://'+opened.get('http','')
            parsed=urlparse(endpoint)
            if parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.scheme not in ('http','ws'):
                raise ValueError('无效本机调试地址')
            browser=pw.chromium.connect_over_cdp(endpoint,is_local=True,timeout=30000)
            context=browser.contexts[0]
            page=context.new_page()
            try:
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
                        check_own_post(page)
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
    patches=[proxy(s) for s in proxies]
    if any(not re.fullmatch(r'[a-zA-Z0-9_-]{20,512}',s) for s in tokens):
        raise ValueError('token 格式不正确，请每行粘贴一个完整 token。')
    if action=='delete' and any(state(i)['state']!='delete' for i in ids):
        raise ValueError('只能删除待删除状态的窗口。')
    if action=='delete' and body.get('confirm') is not True:
        raise ValueError('请确认删除选中的窗口。')
    return action,ids,names,patches,tokens,str(body.get('remark',''))[:2000]

def manual_check(body):
    from worker import bit, publisher

    pid = body.get('id')
    action = body.get('action')
    if not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', pid):
        raise ValueError('请选择有效窗口。')
    if action not in ('open', 'confirm'):
        raise ValueError('无效的手动检测操作。')

    with publisher.lock:
        if publisher.thread and publisher.thread.is_alive():
            raise ValueError('发布队列正在执行，请完成或停止后再操作。')
        if not operation_lock.acquire(blocking=False):
            raise ValueError('窗口操作正在执行，请稍候。')
    try:
        if action == 'open':
            bit('/browser/open', {'id': pid})
            return
        labels = {'pending': '待配置', 'ready': '可使用', 'delete': '待删除'}
        status = body.get('state')
        if not isinstance(status, str) or status not in labels:
            raise ValueError('请选择有效的窗口状态。')
        if body.get('confirmed') is not True:
            raise ValueError('请确认手动检测结果。')
        if not bit('/browser/detail', {'id': pid}):
            raise ValueError('窗口不存在，请刷新列表。')
        mark(pid, status, f'人工检测：已手动标为「{labels[status]}」。')
    finally:
        operation_lock.release()


def start(body):
    from worker import publisher
    # Shared gate prevents posting and window operations from racing.
    with publisher.lock:
        if publisher.thread and publisher.thread.is_alive():
            raise ValueError('发布队列正在执行，请完成或停止后再管理窗口。')
        if not operation_lock.acquire(blocking=False):
            raise ValueError('窗口操作正在执行，请稍候。')
        try:
            args=prepare(body)
            action, ids, *_ = args
            workers = min(DETECTION_WORKERS, len(ids)) if action == 'detect' else 1
            job.update(running=True, results=[], action=action, total=len(ids), workers=workers)
            threading.Thread(target=perform,args=args,daemon=True).start()
        except Exception:
            operation_lock.release()
            raise

def detect_windows(ids, tokens):
    with ThreadPoolExecutor(max_workers=min(DETECTION_WORKERS, len(ids))) as pool:
        pending = {
            pool.submit(inspect_window, pid, tokens[index] if tokens else ''): pid
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
            try:
                if action=='create':
                    result=bit('/browser/update',dict(name=names[index],platform='https://x.com',platformIcon='x.com',url='https://x.com/home',remark=remark,browserFingerPrint={},**(patches[index] if patches else {})))
                    pid=result['id']
                    mark(pid,'pending','窗口已创建，等待配置与检测。')
                elif action=='delete':
                    if bit('/browser/pids/alive',{'ids':[pid]}).get(pid):
                        bit('/browser/close',{'id':pid})
                    bit('/browser/delete',{'id':pid})
                    with store.connect() as db:
                        db.execute('DELETE FROM windows WHERE id=?',(pid,))
                elif action in ('configure','edit'):
                    if patches or tokens:
                        mark(pid,'pending','配置已开始，等待重新检测。')
                        # Close before saving so a running session cannot overwrite the new settings.
                        if bit('/browser/pids/alive',{'ids':[pid]}).get(pid):
                            bit('/browser/close',{'id':pid})
                    patch={'ids':[pid],'browserFingerPrint':{}}
                    if patches:
                        patch.update(patches[index])
                    if action=='edit':
                        patch['remark']=remark
                    bit('/browser/update/partial',patch)
                if action!='delete' and (action!='edit' or patches):
                    inspect_window(pid,tokens[index] if tokens else '')
                current=state(pid) if action!='delete' else {'reason':'已删除'}
                job['results'].append({'id':pid,'name':names[index] if action=='create' else pid,'message':current['reason']})
            except Exception:
                job['results'].append({'id':pid,'message':'操作失败，请检查比特浏览器、窗口状态和输入；未输出凭据。'})
    finally:
        tokens.clear()
        patches.clear()
        job['running']=False
        operation_lock.release()
