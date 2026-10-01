"""WhatsApp Bridge 客户端：同一会话只复用／启动一个 Bridge，不重复开端口。"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import requests
from core_new import get_logger

BRIDGE_DIR_NAME = 'whatsapp_bridge'
STARTUP_TIMEOUT_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 15
PORT_FIRST = 8765
PORT_LAST = 8784
logger = get_logger('whatsapp_bridge_client')
_BRIDGE_START_LOCK = threading.RLock()

COMMON_NODE_INSTALL_PATHS = (
    r'C:\Program Files\nodejs\node.exe',
    r'C:\Program Files (x86)\nodejs\node.exe',
)


def _find_node_executable() -> Optional[str]:
    found = shutil.which('node')
    if found:
        return found
    for candidate in COMMON_NODE_INSTALL_PATHS:
        if Path(candidate).exists():
            return candidate
    local = os.environ.get('LOCALAPPDATA')
    if local:
        for candidate in (Path(local) / 'nvm').glob('v*/node.exe'):
            if candidate.exists():
                return str(candidate)
    return None


def build_node_popen_args(bridge_dir: Path, script_name: str = 'server.js') -> list:
    node = _find_node_executable()
    if not node:
        raise FileNotFoundError('未找到 Node.js。请安装 Node.js 后重新打开 App。')
    return [node, script_name]


def _find_free_port() -> int:
    for port in range(PORT_FIRST, PORT_LAST + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(('127.0.0.1', port)) != 0:
                return port
    raise RuntimeError(f'端口 {PORT_FIRST}–{PORT_LAST} 均被占用，未启动新 Bridge。')


class WhatsAppBridgeStartupError(RuntimeError):
    pass


class WhatsAppBridgeClient:
    """保留既有公开方法；启动前复用同 SESSION_PATH 的唯一 Bridge。"""

    def __init__(self, chrome_profile_dir: Path, headless: bool = True,
                 dry_run: bool = True, bridge_project_dir: Optional[Path] = None):
        self.session_dir = Path(chrome_profile_dir)
        self.headless = headless
        self.dry_run = dry_run
        self.bridge_dir = bridge_project_dir or (Path(__file__).resolve().parent / BRIDGE_DIR_NAME)
        self.port: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None
        self._adopted_process = False

    @staticmethod
    def _path_key(value) -> str:
        return str(value or '').replace('/', '\\').rstrip('\\').casefold()

    def _same_session(self, payload: dict) -> bool:
        return self._path_key(payload.get('session_path')) == self._path_key(self.session_dir)

    @staticmethod
    def _state(payload: Optional[dict]) -> str:
        return str((payload or {}).get('status', '')).upper()

    def _read_status(self, port: int, timeout: float = 1.5) -> Optional[dict]:
        try:
            response = requests.get(f'http://127.0.0.1:{port}/status', timeout=timeout)
            payload = response.json() if response.ok else None
            return payload if isinstance(payload, dict) else None
        except (requests.RequestException, ValueError):
            return None

    def _matching_bridges(self):
        matches = []
        for port in range(PORT_FIRST, PORT_LAST + 1):
            payload = self._read_status(port)
            if payload and self._same_session(payload):
                matches.append((port, payload))
        return matches

    def _forward_bridge_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        try:
            for raw in iter(self.process.stdout.readline, ''):
                line = raw.rstrip()
                if line:
                    logger.info('[Bridge] %s', line)
        except Exception as exc:
            logger.warning('读取 Bridge 输出失败：%s', exc)

    def _shutdown_port(self, port: int) -> bool:
        try:
            requests.post(f'http://127.0.0.1:{port}/shutdown', timeout=4)
        except requests.RequestException:
            pass
        for _ in range(15):
            if self._read_status(port, timeout=0.5) is None:
                return True
            time.sleep(0.2)
        return False

    def _adopt(self, port: int, payload: dict) -> None:
        self.port = port
        self.process = None
        self._adopted_process = True
        logger.info('复用现有 WhatsApp Bridge：port=%s state=%s session=%s',
                    port, self._state(payload), payload.get('session_path'))

    def start_browser(self) -> None:
        if self.dry_run:
            logger.info('演示模式：跳过 WhatsApp Bridge 启动')
            return
        with _BRIDGE_START_LOCK:
            if self.process and self.process.poll() is None:
                return
            if self._adopted_process and self.port:
                payload = self._read_status(self.port)
                if payload and self._same_session(payload) and self._state(payload) != 'ERROR':
                    return
                self._adopted_process = False
                self.port = None

            matches = self._matching_bridges()
            usable = [(port, data) for port, data in matches if self._state(data) != 'ERROR']
            errors = [(port, data) for port, data in matches if self._state(data) == 'ERROR']

            if len(usable) > 1:
                raise WhatsAppBridgeStartupError(
                    '发现多个使用同一 WhatsApp 会话的 Bridge，未启动新实例。端口：' +
                    ', '.join(str(port) for port, _ in usable)
                )
            if usable:
                self._adopt(*usable[0])
                return

            # 旧 ERROR Bridge 不可复用：先请求服务端正常退出，确认释放后才启动一次。
            for port, payload in errors:
                logger.warning('清理同会话错误 Bridge：port=%s detail=%s',
                               port, payload.get('detail', ''))
                if not self._shutdown_port(port):
                    raise WhatsAppBridgeStartupError(
                        f'端口 {port} 的错误 Bridge 无法停止；未启动第二个实例。'
                    )

            if not self.bridge_dir.exists() or not (self.bridge_dir / 'server.js').is_file():
                raise WhatsAppBridgeStartupError(f'未找到 Bridge 服务端：{self.bridge_dir / "server.js"}')
            if not (self.bridge_dir / 'node_modules').exists():
                raise WhatsAppBridgeStartupError('未检测到 whatsapp_bridge/node_modules，请在该目录执行 npm install。')

            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.port = _find_free_port()
            env = {**os.environ, 'PORT': str(self.port), 'SESSION_PATH': str(self.session_dir),
                   'HEADLESS': 'true' if self.headless else 'false'}
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            try:
                self.process = subprocess.Popen(
                    build_node_popen_args(self.bridge_dir), cwd=str(self.bridge_dir), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding='utf-8', errors='replace', bufsize=1, creationflags=flags,
                )
                threading.Thread(target=self._forward_bridge_output, daemon=True,
                                 name='WhatsAppBridgeOutput').start()
                self._wait_for_bridge_ready()
            except Exception:
                self._terminate_owned_process()
                raise

    def _wait_for_bridge_ready(self) -> None:
        deadline = time.time() + STARTUP_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                raise WhatsAppBridgeStartupError('WhatsApp Bridge 进程异常退出，请查看日志。')
            payload = self._read_status(self.port)
            if payload:
                state = self._state(payload)
                if state == 'ERROR':
                    raise WhatsAppBridgeStartupError(
                        str(payload.get('detail') or 'WhatsApp Client 初始化失败')
                    )
                # READY 表示已登录；QR／AUTHENTICATED／STARTING 表示服务已启动，
                # GUI 可继续显示二维码或轮询状态，但不能再启动另一个端口。
                logger.info('WhatsApp Bridge 已启动：port=%s state=%s', self.port, state)
                return
            time.sleep(0.5)
        raise WhatsAppBridgeStartupError('WhatsApp Bridge 启动超时。')

    def _get_status(self, timeout: int = 5) -> Optional[dict]:
        if not self.port:
            return None
        payload = self._read_status(self.port, timeout)
        if payload:
            logger.info('Bridge 状态回传：port=%s payload=%s', self.port, payload)
        return payload

    def is_logged_in(self, timeout: int = 15) -> bool:
        return True if self.dry_run else self._state(self._get_status(timeout)) == 'READY'

    def is_browser_alive(self) -> bool:
        if self.dry_run:
            return True
        if self._adopted_process:
            return bool(self._get_status(2))
        return bool(self.process and self.process.poll() is None)

    def get_login_qr_data_url(self) -> Optional[str]:
        if self.dry_run or not self.port:
            return None
        try:
            response = requests.get(f'http://127.0.0.1:{self.port}/qr', timeout=5)
            return response.json().get('qr_data_url') if response.ok else None
        except (requests.RequestException, ValueError):
            return None

    def search_groups(self, keyword: str = '') -> list:
        return self.list_groups(keyword)

    def list_groups(self, keyword: str = '') -> list:
        if self.dry_run:
            groups = [
                {'id': '111111111-1111111111@g.us', 'name': '工程告警群'},
                {'id': '222222222-2222222222@g.us', 'name': '运维通知群'},
                {'id': '333333333-3333333333@g.us', 'name': '项目工程讨论组'},
            ]
            return [g for g in groups if not keyword or keyword.casefold() in g['name'].casefold()]
        if not self.is_logged_in():
            return []
        try:
            response = requests.get(f'http://127.0.0.1:{self.port}/list_groups',
                                    params={'keyword': keyword}, timeout=REQUEST_TIMEOUT_SECONDS)
            return response.json().get('groups', []) if response.ok else []
        except (requests.RequestException, ValueError):
            return []

    def verify_group_exists(self, group_exact_name: str) -> bool:
        if self.dry_run:
            return True
        if not self.is_logged_in():
            return False
        try:
            response = requests.get(f'http://127.0.0.1:{self.port}/verify_group',
                                    params={'name': group_exact_name}, timeout=REQUEST_TIMEOUT_SECONDS)
            return bool(response.ok and response.json().get('found', False))
        except (requests.RequestException, ValueError):
            return False

    def send_text_and_image(self, group_exact_name: str, text: str,
                            image_path: Optional[str] = None) -> dict:
        if self.dry_run:
            return {'status': 'SENT', 'detail': 'dry_run'}
        if not self.is_browser_alive():
            return {'status': 'FAILED', 'detail': 'WhatsApp Bridge 未启动'}
        if not self.is_logged_in():
            return {'status': 'FAILED', 'detail': 'WhatsApp 尚未登录'}
        try:
            response = requests.post(f'http://127.0.0.1:{self.port}/send',
                                     json={'group_name': group_exact_name, 'text': text,
                                           'image_path': image_path}, timeout=60)
            return response.json() if response.ok else {
                'status': 'FAILED', 'detail': f'Bridge HTTP {response.status_code}'
            }
        except (requests.RequestException, ValueError) as exc:
            return {'status': 'FAILED', 'detail': f'调用 Bridge 失败：{exc}'}

    def _terminate_owned_process(self) -> None:
        if not self.process:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        finally:
            self.process = None

    def quit(self) -> None:
        if self.dry_run or not self.port:
            return
        # 无论本次进程是新建还是复用，App 正常退出时均请求关闭它，
        # 避免下次启动积累多个 node.exe/server.js。
        self._shutdown_port(self.port)
        if not self._adopted_process:
            self._terminate_owned_process()
        self.port = None
        self._adopted_process = False
