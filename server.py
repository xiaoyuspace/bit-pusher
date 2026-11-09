import errno
import json
import secrets
import socket
import os
import subprocess
import sys
from threading import Lock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import store
import batch
import window_manager as wm
from worker import profiles, publisher

VERSION = '2026-11-09-parallel-detection'
PORT = 8766
TOKEN = secrets.token_urlsafe(32)
PICKER_LOCK = Lock()


def pick_batch_path(kind):
    if kind not in ('folder', 'text_file'):
        raise ValueError('无效的素材选择类型。')
    if not PICKER_LOCK.acquire(blocking=False):
        raise ValueError('已有选择窗口打开，请先完成或取消。')
    try:
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(store.ROOT / 'server.py'), '--pick-batch-path', kind],
            capture_output=True, encoding='utf-8', check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, ValueError):
        raise ValueError('无法打开本地选择窗口，请确认在运行发布台的 Windows 桌面上操作。') from None
    finally:
        PICKER_LOCK.release()


def show_batch_picker(kind):
    from tkinter import Tk, filedialog

    root = Tk()
    try:
        root.withdraw()
        root.attributes('-topmost', True)
        if kind == 'folder':
            return filedialog.askdirectory(parent=root, title='选择视频文件夹', mustexist=True)
        return filedialog.askopenfilename(parent=root, title='选择文案 TXT 文件', filetypes=[('TXT 文本文件', '*.txt')])
    finally:
        root.destroy()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):
        pass

    def send(self,value,status=200,mime='application/json; charset=utf-8'):
        data = value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}',f'localhost:{PORT}')

    def do_GET(self):
        if not self.host_ok():
            return self.send({'error':'Host rejected'},403)
        path = urlparse(self.path).path
        if path == '/api/state':
            return self.send({'version':VERSION,'pid':os.getpid(),'token':TOKEN,'tasks':store.tasks(),'running':bool(publisher.thread and publisher.thread.is_alive()),'current':publisher.current,'window_job':wm.job})
        if path == '/api/profiles':
            try:
                return self.send({'profiles':wm.listing()})
            except Exception:
                return self.send({'error':'无法连接比特浏览器，请确认客户端已登录且本地 API 54345 已开启。'},502)
        files = {'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
        if path in files:
            name,mime = files[path]
            return self.send((store.ROOT/'static'/name).read_bytes(),mime=mime)
        return self.send({'error':'Not found'},404)

    def do_POST(self):
        if not self.host_ok() or self.headers.get('X-Publisher-Token') != TOKEN:
            return self.send({'error':'请求校验失败，请刷新页面。'},403)
        try:
            size = int(self.headers.get('Content-Length',0))
            if not 0 <= size <= 200000:
                raise ValueError('请求过大。')
            body = json.loads(self.rfile.read(size) or b'{}')
            if self.path == '/api/windows/manual-check':
                wm.manual_check(body)
                return self.send({'ok':True})
            if self.path == '/api/windows':
                wm.start(body)
                return self.send({'ok':True})
            if self.path == '/api/batch/pick':
                return self.send({'path':pick_batch_path(body.get('kind'))})
            if self.path in ('/api/batch/preview','/api/batch/create'):
                wm.require_ready(body.get('profiles'))
                prepared = batch.plan(body.get('folder'),body.get('text_file'),body.get('profiles'),profiles())
                if self.path == '/api/batch/preview':
                    return self.send(prepared)
                if body.get('signature') != prepared['signature']:
                    raise ValueError('素材或窗口信息已变化，请重新预览。')
                ids = batch.create(prepared)
                publisher.run(ids)
                return self.send({'ids':ids,'failure_folder':prepared['failure_folder']})
            if self.path == '/api/task':
                wm.require_ready([body.get('profile_id')])
                return self.send({'id':store.create_task(body)})
            if self.path == '/api/run':
                publisher.run(body.get('ids'))
            elif self.path == '/api/tasks/delete':
                with publisher.lock:
                    deleted = store.delete_tasks(body.get('ids'), current=publisher.current)
                return self.send({'deleted':deleted})
            elif self.path == '/api/stop':
                publisher.stop()
            elif self.path == '/api/resolve':
                with publisher.lock:
                    store.resolve(body.get('id'),str(body.get('url','')).strip())
            else:
                return self.send({'error':'Not found'},404)
            return self.send({'ok':True})
        except (ValueError,TypeError,KeyError) as e:
            return self.send({'error':str(e)},400)
        except Exception:
            return self.send({'error':'本地操作失败，请检查文件路径及目录权限。'},500)


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--pick-batch-path':
        print(json.dumps(show_batch_picker(sys.argv[2]), ensure_ascii=False))
        raise SystemExit(0)
    try:
        server = LocalServer(('127.0.0.1', PORT), Handler)
    except OSError as error:
        if error.errno != errno.EADDRINUSE and getattr(error, 'winerror', None) != 10048:
            raise
        print(f'端口 {PORT} 已被占用。若比特发布台已在运行，请直接打开 http://127.0.0.1:{PORT}。', file=sys.stderr)
        raise SystemExit(1) from None
    store.init()
    print(f'Bit Publisher: http://127.0.0.1:{PORT}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        publisher.stop()
        server.server_close()
