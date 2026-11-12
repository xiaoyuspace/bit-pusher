"""BitBrowser Local API + normal X web composer; no credentials exported."""
import json
import re
import threading
import time
import urllib.request
from urllib.parse import urlparse
import store

API = 'http://127.0.0.1:54345'

# X uses the attachment status label for the upload phase.
ATTACHMENT_READY = re.compile(
    r'(?:[:：]\s*|^)(?:'
    r'Ready|Tutto pronto|Pronto|List[oa]|Todo listo|Preparad[oa]|'
    r'Prêt(?:e)?|Terminé|Bereit|Fertig|Klaar|Gereed|'
    r'Gotow[ey]|Hazır|Готово|Готовий|Siap|Sedia|'
    r'พร้อม(?:แล้ว)?|Sẵn sàng|Đã sẵn sàng|'
    r'準備完了|準備ができました|준비 완료|준비됨|'
    r'(?:已)?就[绪緒]|已[备備]妥|上[传傳]完成|جاهز(?:ة)?|तैयार'
    r')\s*[.!。！]?\s*$', re.I
)

ATTACHMENT_ERROR = re.compile(
    r'(?:[:：]\s*|^)(?:'
    r'Something went wrong|Upload failed|Processing failed|Ada kesalahan teknis|'
    r'Qualcosa è andato storto|Caricamento non riuscito|'
    r'Algo salió mal|Error al cargar|Une erreur s.est produite|Échec du téléversement|'
    r'Etwas ist schiefgelaufen|Upload fehlgeschlagen|Algo deu errado|Falha no upload|'
    r'出了点问题|出現錯誤|出现错误|上传失败|上傳失敗|处理失败|處理失敗|'
    r'問題が発生しました|アップロードに失敗しました|문제가 발생했습니다|업로드 실패'
    r')\s*[.!。！]?\s*$', re.I
)


def bit(path, body):
    req = urllib.request.Request(API + path, json.dumps(body).encode(), {'Content-Type':'application/json'})
    # Local API must never be routed through an environment HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as r:
        data = json.load(r)
    if data.get('success') is not True:
        raise RuntimeError('比特浏览器 API 失败：' + str(data.get('msg') or '请检查客户端状态')[:200])
    return data.get('data') or {}


def profiles():
    result = []
    seen = set()
    for page in range(100):
        data = bit('/browser/list', {'page':page,'pageSize':100})
        rows = data.get('list', [])
        fresh = [r for r in rows if r.get('id') not in seen]
        for row in fresh:
            seen.add(row['id'])
            result.append({**{k:row.get(k) for k in ('id','name','seq')},
                           'proxy_ip': (row.get('host') or '') if row.get('proxyType') != 'noproxy' else '',
                           'proxy_user': (row.get('proxyUserName') or '') if row.get('proxyType') != 'noproxy' else '',
                           'remark': row.get('remark') or ''})
        if not fresh or len(result) >= int(data.get('totalNum', len(result))):
            return result
    raise RuntimeError('窗口分页超出限制。')


def post_result(payload, expected_text):
    """Only trust the newly created tweet result, never an arbitrary timeline ID."""
    try:
        result = payload['data']['create_tweet']['tweet_results']['result']
        if result.get('__typename') == 'TweetWithVisibilityResults':
            result = result['tweet']
        tweet_id = result['rest_id']
        legacy = result['legacy']
        if not str(tweet_id).isdigit():
            return None
        # Verify content and media from this submission; no account comparison.
        actual = legacy.get('full_text','')
        if not actual or actual[:min(30,len(expected_text))] != expected_text[:30]:
            return None
        media = (legacy.get('extended_entities') or {}).get('media', [])
        if not any(m.get('type') in ('video','animated_gif') for m in media):
            return None
        return f'https://x.com/i/status/{tweet_id}'
    except (KeyError, TypeError, AttributeError):
        return None


def rejection_reason(payload):
    if not isinstance(payload, dict):
        return None
    for error in payload.get('errors', []) or []:
        if isinstance(error, dict) and error.get('code') in (64, 326):
            return 'X 明确拒绝发布：账号被冻结或受限（错误码 ' + str(error['code']) + '）。'
    return None


class AutoPublishBlocked(ValueError):
    pass


class AccountUnavailable(AutoPublishBlocked):
    pass


class LoginRequired(AutoPublishBlocked):
    pass


# Match a known title and its intended action together. Generic OK/Yes is not a rule.
CONFIRMATION_PROMPTS = (
    # Downloadable-video and terms-update notices.
    ('Introducing Downloadable Videos', 'Got it'),
    ('Presentamos videos descargables', 'Entendido'),
    ('Présentation des vidéos téléchargeables', 'Compris'),
    ('Apresentamos os vídeos para download', 'Entendi'),
    ('Jetzt neu: herunterladbare Videos', 'Verstanden'),
    ('Introduzione ai video scaricabili', 'Ho capito'),
    ('Updates to our Terms of Service', 'Got it'),
    ('Actualizaciones de nuestros Términos de Servicio', 'Entendido'),
    ('Mises à jour de nos Conditions d’utilisation', 'Compris'),
    ('Atualizações dos nossos Termos de Serviço', 'Entendi'),
    ('Aktualisierungen unserer Nutzungsbedingungen', 'Verstanden'),
    ('Aggiornamenti ai nostri Termini di servizio', 'Ho capito'),
    ('服务条款更新', '知道了'),
    ('服務條款更新', '知道了'),
    ('利用規約の更新', '了解しました'),
    ('서비스 약관 업데이트', '알겠습니다'),
    # Review an existing contact. The alternative actions replace it.
    ('Review your email', "Yes, that's correct"),
    ('Tinjau email Anda', 'Ya, benar'),
    ('Revisa la dirección de correo que proporcionaste', 'Sí, es correcto'),
    ('Vérifiez votre adresse e-mail', 'Oui, c’est correct'),
    ('Revise seu e-mail', 'Sim, está correto'),
    ('Überprüfe deine E-Mail-Adresse', 'Ja, das ist korrekt'),
    ('Controlla il tuo indirizzo email', 'Sì, è corretto'),
    ('检查你的电子邮件地址', '是的，正确'),
    ('確認你的電子郵件地址', '是的，正確'),
    ('Tinjau nomor ponsel Anda', 'Ya, itu nomor saya'),
    ('Revisa tu número de teléfono', 'Sí, ese es mi número'),
    ('Vérifiez votre numéro de téléphone', 'Oui, c’est mon numéro'),
    ('Revise seu número de telefone', 'Sim, esse é meu número'),
    ('Überprüfe deine Telefonnummer', 'Ja, das ist meine Nummer'),
    ('Controlla il tuo numero di telefono', 'Sì, è il mio numero'),
    ('检查你的手机号码', '是的，这是我的号码'),
    ('確認你的手機號碼', '是的，這是我的號碼'),
    # User chose the opt-in button for this specific prompt.
    ('Tienes el control', 'Activar los anuncios personalizados'),
    ('You’re in control', 'Turn on personalized ads'),
    ("You're in control", 'Turn on personalized ads'),
    ('Vous avez le contrôle', 'Activer les publicités personnalisées'),
    ('Você está no controle', 'Ativar anúncios personalizados'),
    ('Du hast die Kontrolle', 'Personalisierte Anzeigen aktivieren'),
    ('Hai il controllo', 'Attiva gli annunci personalizzati'),
)

ACCEPT_COOKIES = re.compile(
    r'^(?:Accept all cookies|Accepter tous les cookies|Aceptar todas las cookies|'
    r'Accetta tutti i cookie|Alle Cookies akzeptieren|Aceitar todos os cookies|'
    r'Accepteer alle cookies|Terima semua cookie|Tüm çerezleri kabul et|'
    r'Принять все файлы cookie|すべてのCookieを受け入れる|모든 쿠키 허용|'
    r'接受所有(?:\s*Cookie)?|同意所有(?:\s*Cookie)?|接受所有 Cookie)$', re.I
)


def dismiss_optional_prompt(dialog):
    text = dialog.inner_text().strip()
    if re.search(r'premium|subscribe|subscription|订阅|訂閱|套餐|suscripci|abonnement|assinatura|abbonamento|abonelik|구독|サブスクリプション', text, re.I):
        for name in (r'Close(?: dialog| modal)?|Dismiss|关闭(?:弹窗)?|關閉|Cerrar|Fermer|Schließen|Fechar|Chiudi|Tutup|Sluiten|Kapat|Закрыть|閉じる|닫기|إغلاق',
                     r'Not now|Maybe later|No thanks|暂不|暫不|以后再说|Ahora no|Plus tard|Pas maintenant|Nicht jetzt|Agora não|Non ora|Nanti saja|今はしない|나중에'):
            close = dialog.get_by_role('button', name=re.compile(r'^(?:' + name + r')$', re.I)).filter(visible=True)
            if close.count() == 1:
                close.click(timeout=10000)
                dialog.wait_for(state='hidden', timeout=10000)
                return True
        raise AutoPublishBlocked('订阅弹窗没有可识别的关闭按钮，已停止，不会购买套餐。')
    # Only acknowledge informational upload/media messages; never accept account/payment changes.
    if re.search(r'video|media|upload|视频|影片|素材|上传|上傳|vidéo|vídeo|caricamento|Medien|Unggah', text, re.I) and not re.search(r'password|payment|purchase|delete|verification|phone|terms|agree|email|consent|privacy|contraseña|pago|eliminar|verificaci|conditions|paiement|supprimer|passwort|zahlung|löschen|password|acquist|senha|pagamento|hapus|密码|密碼|付费|付款|购买|删除|刪除|验证|驗證|手机号|協議|协议|同意', text, re.I):
        ok = dialog.get_by_role('button', name=re.compile(r'^(OK|Got it|Okay|确定|確定|知道了|好的|Entendido|Compris|Verstanden|Entendi|Ho capito|Mengerti|Paham|了解しました|알겠습니다|Понятно)$', re.I))
        if ok.count() == 1 and ok.is_visible():
            ok.click(timeout=10000)
            dialog.wait_for(state='hidden', timeout=10000)
            return True
    return False


def handle_known_prompts(page):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    # User confirmed all bound numbers remain in use and authorized this exact action.
    title = page.get_by_text('Review your phone', exact=True)
    if title.count() and title.first.is_visible():
        yes = page.get_by_role('button', name='Yes, that is my number', exact=True)
        if yes.count() != 1 or not yes.is_visible():
            raise AutoPublishBlocked('手机号确认弹窗结构发生变化，无法安全自动确认。')
        try:
            yes.click(timeout=1500)
        except PlaywrightTimeout:
            # X sometimes leaves a transparent layer over the confirmation button.
            if not yes.is_enabled():
                raise AutoPublishBlocked('手机号确认按钮暂不可用。') from None
            yes.focus(timeout=3000)
            if not yes.evaluate('(button) => button === document.activeElement'):
                raise AutoPublishBlocked('手机号确认按钮未获得键盘焦点。') from None
            yes.press('Enter', timeout=3000)
        title.first.wait_for(state='hidden', timeout=15000)
        return True

    inaccessible_prompt = None
    for title_text, button_text in CONFIRMATION_PROMPTS:
        title = page.get_by_text(title_text, exact=True)
        if not title.count() or not title.first.is_visible():
            continue
        dialog = page.locator('[role="dialog"],[role="alertdialog"]').filter(has=title)
        confirm = dialog.get_by_role('button', name=button_text, exact=True).filter(visible=True)
        if confirm.count() == 1:
            try:
                confirm.click(timeout=1500)
            except PlaywrightTimeout:
                if title_text == 'Updates to our Terms of Service' and confirm.is_enabled():
                    confirm.focus(timeout=3000)
                    if confirm.evaluate('(button) => button === document.activeElement'):
                        confirm.press('Enter', timeout=3000)
                        title.first.wait_for(state='hidden', timeout=15000)
                        return True
            else:
                title.first.wait_for(state='hidden', timeout=15000)
                return True
        inaccessible_prompt = title_text

    handled = False
    if urlparse(page.url).hostname in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'):
        accept = page.get_by_role('button', name=ACCEPT_COOKIES).filter(visible=True)
        if accept.count() == 1:
            try:
                accept.click(timeout=1500)
            except PlaywrightTimeout:
                if not accept.is_enabled():
                    raise AutoPublishBlocked('Cookie 提示的确认按钮暂不可用。') from None
                accept.focus(timeout=3000)
                if not accept.evaluate('(button) => button === document.activeElement'):
                    raise AutoPublishBlocked('Cookie 提示的确认按钮未获得键盘焦点。') from None
                accept.press('Enter', timeout=3000)
            accept.wait_for(state='hidden', timeout=10000)
            handled = True
    for dialog in page.get_by_role('dialog').all() + page.get_by_role('alertdialog').all():
        if dialog.is_visible() and dialog.get_by_test_id('tweetTextarea_0').count() == 0:
            handled = dismiss_optional_prompt(dialog) or handled
    if inaccessible_prompt and not handled:
        raise AutoPublishBlocked(f'弹窗「{inaccessible_prompt}」的确认按钮暂不可操作。')
    return handled


def check_unknown_prompt(page):
    dialog = page.locator('[role="dialog"],[role="alertdialog"]')
    prompts = dialog.evaluate_all("""nodes => {
        const visible = node => node.getClientRects().length > 0 &&
            node.getBoundingClientRect().height > 0 && getComputedStyle(node).visibility === 'visible';
        return nodes.filter(node => !node.querySelector('[data-testid="tweetTextarea_0"]'))
            .map(node => {
                const heading = [...node.querySelectorAll('h1,h2,h3,[role="heading"]')].find(visible);
                const buttons = [...node.querySelectorAll('button,[role="button"]')].filter(visible)
                    .map(button => button.getAttribute('aria-label') || button.innerText.trim()).filter(Boolean);
                if (!heading && !buttons.length && !visible(node)) return null;
                const label = heading?.innerText || node.innerText.split('\\n').find(line => line.trim()) || '';
                return label.trim() || buttons.length ? {label, buttons: buttons.slice(0, 4)} : null;
            }).filter(Boolean);
    }""")
    if not prompts:
        return

    def redact(text):
        text = re.sub(r'https?://\S+|\S+@\S+|[+*\d][*\d ()-]{5,}|[A-Za-z0-9_-]{20,}', '[已隐藏]', text)
        return text.strip()[:80]

    prompt = prompts[-1]
    label = redact(prompt['label']) or '无标题'
    buttons = '、'.join(redact(text) for text in prompt['buttons']) or '未识别'
    raise AutoPublishBlocked(f'发现未识别的弹窗「{label}」（按钮：{buttons}），请人工确认。')


def check_access(page):
    url = urlparse(page.url)
    text = page.locator('body').inner_text(timeout=5000)
    if any(term in text.lower() for term in ('performing security verification', 'verify you are human', 'verify you are not a bot')):
        raise AccountUnavailable('无法自动发帖：X / Cloudflare 安全验证未通过，需要手动完成验证。')
    if url.path.startswith('/account/access'):
        raise AccountUnavailable('无法自动发帖：X 要求完成账号访问检查，请在该窗口手动处理。')
    if re.search(r'your account is suspended|your account has been suspended|tu cuenta est[áa] suspendida|akun anda ditangguhkan|你的账号已被冻结|您的账号已被冻结', text, re.I):
        raise AccountUnavailable('账号已被冻结，无法发帖。')
    if url.path.startswith('/i/flow/login'):
        raise LoginRequired('无法自动发帖：该窗口未登录 X，请先手动登录。')


def like_published_post(page, url):
    """Like only the permalink returned by the successful publishing request."""
    match = re.fullmatch(r'https://x\.com/i/status/([0-9]+)', url)
    if not match:
        raise ValueError('发布成功链接格式异常。')
    post_id = match.group(1)
    page.goto(url, wait_until='domcontentloaded', timeout=30000)
    if urlparse(page.url).hostname not in ('x.com', 'www.x.com'):
        raise ValueError('帖子详情页不在 X。')

    article = page.get_by_test_id('primaryColumn').get_by_role('article').filter(
        has=page.locator(f'a[href$="/status/{post_id}"] time')
    )
    like = article.get_by_test_id('like')
    unlike = article.get_by_test_id('unlike')
    like.or_(unlike).wait_for(state='visible', timeout=15000)
    if unlike.is_visible():
        return
    handle_known_prompts(page)
    if not unlike.is_visible():
        like.click(timeout=10000)
    unlike.wait_for(state='visible', timeout=10000)


class Publisher:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.current = None
        self.activity = None
        self.closing = {}

    def closing_task_ids(self):
        # Call while holding self.lock.
        return [tid for tid, timer in self.closing.values() if timer.is_alive()]

    def schedule_close(self, profile_id, tid, message, url):
        def close_window():
            try:
                bit('/browser/close', {'id': profile_id})
                close_message = ' 比特窗口已关闭。'
            except Exception:
                close_message = ' 比特窗口未能自动关闭，请手动关闭。'
            store.update(tid, 'success', message + close_message, url)

        timer = threading.Timer(10, close_window)
        timer.daemon = True
        with self.lock:
            self.closing[profile_id] = (tid, timer)
            timer.start()

    def wait_for_previous_close(self, profile_id):
        previous = self.closing.get(profile_id)
        if previous:
            timer = previous[1]
            while timer.is_alive():
                self.checkpoint()
                timer.join(timeout=0.25)

    def run(self, ids):
        with self.lock:
            import window_manager as wm
            if wm.operation_lock.locked():
                raise ValueError('窗口管理正在执行，请稍候。')
            wm.require_ready([store.get(i)['profile_id'] for i in (ids or [])])
            store.enqueue(ids)
            self.start_worker()

    def reassign_failed(self, task_id, profile_id):
        import window_manager as wm
        with self.lock:
            if wm.operation_lock.locked():
                raise ValueError('窗口管理正在执行，请稍候。')
            if task_id == self.current:
                raise ValueError('任务仍在收尾，请稍后再试。')
            wm.require_ready([profile_id])
            profile = next((item for item in profiles() if item['id'] == profile_id), None)
            if not profile:
                raise ValueError('目标窗口不存在，请刷新窗口列表。')
            store.reassign_failed(task_id, profile_id, profile['name'] or str(profile['seq']))
            self.start_worker()

    def start_worker(self):
        self.stop_event.clear()
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.loop, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.stop_event.set()
            store.cancel_queued()

    def checkpoint(self):
        if self.stop_event.is_set():
            raise InterruptedError('已停止，尚未点击发布。')

    def loop(self):
        try:
            while not self.stop_event.is_set():
                task = store.claim()
                if not task:
                    break
                self.current = task['id']
                self.execute(task)
                self.current = None
                self.activity = None
                with self.lock:
                    self.closing = {pid: item for pid, item in self.closing.items() if item[1].is_alive()}
        finally:
            self.current = None
            self.activity = None
            # Keep window management blocked until all scheduled closes finish.
            for _, timer in self.closing.values():
                timer.join()
            # Close the race where tasks are queued as a worker is exiting.
            with self.lock:
                self.closing.clear()
                self.thread = None
                if not self.stop_event.is_set() and any(t['state']=='queued' for t in store.tasks()):
                    self.thread = threading.Thread(target=self.loop, daemon=True)
                    self.thread.start()

    def execute(self, task, prepare_only=False):
        tid = task['id']
        submitted = False
        page = None
        stage = None
        published_url = None

        def set_stage(label):
            nonlocal stage
            if label != stage:
                stage = label
                self.activity = {'id': tid, 'label': label, 'since': time.time()}

        set_stage('等待同一窗口完成关窗')
        try:
            self.wait_for_previous_close(task['profile_id'])
            self.checkpoint()
            set_stage('检查视频文件')
            import window_manager as wm
            wm.require_ready([task['profile_id']])
            path, digest = store.file_hash(task['video'])
            if digest != task['file_hash']:
                raise ValueError('视频文件在创建任务后发生变化，请重新创建任务。')
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                set_stage('打开比特浏览器窗口')
                opened = bit('/browser/open', {'id':task['profile_id']})
                endpoint = opened.get('ws')
                if not endpoint:
                    endpoint = 'http://' + str(opened.get('http',''))
                parsed = urlparse(endpoint)
                if parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.scheme not in ('http','ws'):
                    raise RuntimeError('比特浏览器未返回有效的本机调试地址。')
                set_stage('连接浏览器调试接口')
                browser = p.chromium.connect_over_cdp(endpoint, timeout=30000, is_local=True)
                if not browser.contexts:
                    raise RuntimeError('浏览器没有可用上下文。')
                set_stage('创建发帖标签页')
                page = browser.contexts[0].new_page()
                page.set_default_timeout(20000)
                self.checkpoint()
                set_stage('加载发帖页面')
                try:
                    page.goto('https://x.com/compose/post', wait_until='domcontentloaded', timeout=60000)
                except Exception:
                    check_access(page)
                    # X can interrupt navigation with a client-side redirect.
                    # The loop below waits for the composer and checks access again.
                set_stage('定位发帖框')
                composer = page.locator('[role="dialog"][aria-modal="true"]').filter(has=page.get_by_test_id('tweetTextarea_0'))
                editor = composer.get_by_test_id('tweetTextarea_0')
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    self.checkpoint()
                    check_access(page)
                    handle_known_prompts(page)
                    if editor.count() == 1 and editor.is_visible():
                        break
                    page.wait_for_timeout(500)
                else:
                    raise TimeoutError('未找到可用的发帖框。')
                if editor.inner_text().strip():
                    raise ValueError('发帖框已有草稿内容，请先在窗口中处理该草稿。')
                set_stage('填写文案')
                editor.fill(task['text'])
                self.checkpoint()
                set_stage('定位视频上传入口')
                upload = composer.locator('input[type="file"][accept*="video"]')
                if upload.count() != 1:
                    raise ValueError('未找到唯一的视频上传入口，请检查页面。')
                if prepare_only:
                    editor.fill('')
                    page.close()
                    store.update(tid,'draft','准备检查通过：窗口、发帖框和上传入口正常，未上传或发布。')
                    return
                set_stage('上传视频文件')
                store.update(tid,'uploading','窗口已打开，正在上传视频，最多等待 20 分钟。')
                upload.set_input_files(path, timeout=120000)
                set_stage('等待 X 完成上传')
                button = composer.get_by_test_id('tweetButton')
                deadline = time.monotonic() + 1200
                ready_since = None
                while time.monotonic() < deadline:
                    self.checkpoint()
                    check_access(page)
                    if not handle_known_prompts(page):
                        check_unknown_prompt(page)
                    if urlparse(page.url).hostname not in ('x.com','www.x.com','twitter.com','www.twitter.com'):
                        raise ValueError('页面离开 X，任务停止。')
                    alerts = page.get_by_role('alert').all_text_contents()
                    if any(re.search(r'failed|error|not supported|too large|失败|不支持|超过|出错', a, re.I) for a in alerts):
                        raise ValueError('页面提示上传/发帖错误，请在窗口中查看。')
                    # Media preview alone is insufficient: processing/progress must finish and Post must enable.
                    preview = composer.locator('[data-testid="attachments"] video, [data-testid="videoPlayer"] video').count() > 0
                    attachments = composer.get_by_test_id('attachments')
                    statuses = attachments.get_by_role('status').all_text_contents()
                    status_texts = statuses or attachments.all_text_contents()
                    for status in status_texts:
                        error = ATTACHMENT_ERROR.search(status.strip())
                        if error:
                            reason = error.group().lstrip(':：').strip()
                            raise ValueError(f'X 的视频附件报错（{reason}），未提交帖子。')
                    uploaded = any(ATTACHMENT_READY.search(status.strip()) for status in status_texts)
                    processing = composer.get_by_text(re.compile(r'^(Uploading|Processing|正在上传|正在处理|处理中|上传中)(?:[ .…0-9%]*)$', re.I)).count() > 0
                    ready = preview and uploaded and not processing and button.count()==1 and button.is_enabled()
                    if ready:
                        set_stage('确认视频就绪')
                    elif processing or uploaded:
                        set_stage('等待 X 处理附件')
                    else:
                        set_stage('等待 X 完成上传')
                    if ready:
                        ready_since = ready_since or time.monotonic()
                        if time.monotonic() - ready_since >= 5:
                            break
                    else:
                        ready_since = None
                    page.wait_for_timeout(1000)
                else:
                    raise TimeoutError('20 分钟内未确认视频处理完成；未点击发布。')
                self.checkpoint()
                # Recheck exact content just before committing the irreversible action.
                if editor.inner_text().strip() != task['text']:
                    raise ValueError('发布框文案与任务不一致，已停止。')
                result = {'url':None, 'rejected':None}
                def on_response(response):
                    if response.request.method != 'POST' or not response.url.split('?')[0].endswith('/CreateTweet'):
                        return
                    try:
                        payload = response.json()
                        if response.ok:
                            result['url'] = post_result(payload,task['text'])
                        result['rejected'] = rejection_reason(payload)
                    except Exception:
                        pass
                page.on('response', on_response)
                set_stage('处理发布前弹窗')
                for _ in range(6):
                    self.checkpoint()
                    if not handle_known_prompts(page):
                        break
                else:
                    raise AutoPublishBlocked('发布前连续出现确认弹窗，需人工检查。')
                check_unknown_prompt(page)
                if not button.is_visible() or not button.is_enabled():
                    raise AutoPublishBlocked('发布按钮尚不可用。')
                button.focus(timeout=10000)
                if not button.evaluate('(e) => e === document.activeElement || e.contains(document.activeElement)'):
                    raise AutoPublishBlocked('发布按钮未获得键盘焦点。')
                self.checkpoint()
                store.update(tid,'submitting','视频就绪，正在提交。结果不明确时不会自动重发。')
                submitted = True  # Durable marker precedes click; crashes cannot trigger a duplicate retry.
                set_stage('点击发布并等待结果')
                button.press('Enter', timeout=15000)
                end = time.monotonic() + 90
                while not result['url'] and not result['rejected'] and time.monotonic() < end:
                    page.wait_for_timeout(500)
                    if not result['url'] and not result['rejected']:
                        check_access(page)
                        handle_known_prompts(page)
                if result['rejected'] and not result['url']:
                    wm.mark(task['profile_id'],'delete',result['rejected'])
                    store.update(tid,'failed',result['rejected'])
                elif result['url']:
                    url = published_url = result['url']
                    set_stage('为原帖点赞')
                    store.update(tid, 'success', '已发布，正在为原帖点赞。', url)
                    try:
                        like_published_post(page, url)
                        message = '已发布并点赞。'
                    except Exception:
                        message = '已发布，未能确认点赞结果。'
                    store.update(tid, 'success', message + ' 等待 10 秒后关闭窗口。', url)
                    self.schedule_close(task['profile_id'], tid, message, url)
                else:
                    store.update(tid,'unknown','已尝试提交但未取得可核验的帖子链接。请检查时间线，勿直接重复发布。')
                # Failed or unconfirmed attempts leave the window available for review.
        except AutoPublishBlocked as e:
            if isinstance(e, AccountUnavailable):
                wm.mark(task['profile_id'],'delete',str(e))
            elif isinstance(e, LoginRequired):
                wm.mark(task['profile_id'],'pending','登录已失效，请重新配置 token。')
            suffix = ' 此前已尝试提交，发布结果仍需人工核实，不会自动重发。' if submitted else ' 本次未提交，已跳过此任务。'
            store.update(tid, 'unknown' if submitted else 'blocked', str(e) + suffix)
        except InterruptedError as e:
            store.update(tid,'unknown' if submitted else 'cancelled',str(e))
        except Exception as e:
            if published_url:
                store.update(tid, 'success', '已发布。收尾操作出现异常，请检查比特窗口。', published_url)
                return
            detail = str(e).replace(task.get('text',''), '[任务文案]') if task.get('text') else str(e)
            detail = detail.split('Call log:')[0].strip()
            message = f'{stage}失败（{type(e).__name__}）：{detail}'
            store.update(tid,'unknown' if submitted else 'failed',message[:1500])


publisher = Publisher()
