"""whatsapp_bridge_client.py —— whatsapp-web.js 桥接客户端(替代 whatsapp_service.WhatsAppSender)。

用法：在 gui.py / engine.py 中把
    from whatsapp_service import WhatsAppSender
换成
    from whatsapp_bridge_client import WhatsAppBridgeClient as WhatsAppSender
其余业务代码（AlertEngine、MainWindow）无需修改，因为本类实现了与
WhatsAppSender 完全一致的公开方法签名：
    start_browser() / is_logged_in() / is_browser_alive() /
    verify_group_exists() / send_text_and_image() / quit()

前置条件：
1. 已安装 Node.js 18+（https://nodejs.org）。
2. 在 whatsapp_bridge/ 目录下执行过 `npm install`（首次会自动下载 Puppeteer
   所需的 Chromium，需要联网，体积约 200MB，请耐心等待）。
3. dry_run=True 时完全不启动 Node 进程，行为与原 Selenium 版本一致。

合规声明：whatsapp-web.js 是非官方库，本质仍是自动化 WhatsApp Web/Business
网页版，不隶属于/不代表 WhatsApp 或 Meta 官方。部署到生产环境前，请自行
确认符合贵组织政策及 WhatsApp 的适用条款。
"""
from __future__ import annotations
import threading
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
import shutil
import requests
import os
from core_new import (get_logger)


BRIDGE_DIR_NAME = "whatsapp_bridge"
STARTUP_TIMEOUT_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 15

logger = get_logger("whatsapp_bridge_client")

# Windows 上 Node.js 官方安装程序的常见默认路径（按可能性排序）
COMMON_NODE_INSTALL_PATHS = [
    r"C:\Program Files\nodejs\node.exe",
    r"C:\Program Files (x86)\nodejs\node.exe",
]



def _find_node_executable() -> Optional[str]:
    """依次尝试：当前 PATH -> 常见安装目录 -> 用户 AppData 下的 nvm-windows 路径。

    返回可直接传给 subprocess 的 node 可执行文件路径（或 "node" 交给 PATH
    解析），找不到则返回 None。
    """
    # 1. 优先用当前进程 PATH 里能找到的 node（最常见、最推荐的情况）
    found = shutil.which("node")
    if found:
        return found

    # 2. 尝试 Windows 常见的默认安装路径
    for candidate in COMMON_NODE_INSTALL_PATHS:
        if Path(candidate).exists():
            logger.warning(
                "当前进程 PATH 中未找到 node，但检测到默认安装路径 %s 存在，"
                "已自动使用该路径。建议重启程序所在的终端/IDE 以刷新 PATH。",
                candidate,
            )
            return candidate

    # 3. 尝试常见的 nvm-windows 安装路径（如果用户通过版本管理器安装 Node.js）
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    nvm_symlink = Path(local_appdata) / "nvm" if local_appdata else None
    if nvm_symlink and nvm_symlink.exists():
        for sub in nvm_symlink.glob("v*"):
            candidate_path = sub / "node.exe"
            if candidate_path.exists():
                logger.warning("检测到通过 nvm-windows 安装的 Node.js：%s", candidate_path)
                return str(candidate_path)

    return None


def build_node_popen_args(bridge_dir: Path, script_name: str = "server.js") -> list:
    """构造启动桥接服务子进程所需的完整命令行参数列表。

    找不到 node 可执行文件时抛出带有明确中文说明的异常，而不是让
    subprocess.Popen 抛出难以理解的 FileNotFoundError。
    """
    node_exe = _find_node_executable()
    if not node_exe:
        raise FileNotFoundError(
            "未能在本机找到 Node.js 可执行文件。请依次检查：\n"
            "1. 打开一个全新的命令提示符窗口，运行 node -v 确认是否有版本号输出；\n"
            "2. 如果全新终端里 node -v 正常，但本程序仍报此错误，通常是因为运行本程序的 "
            "IDE/终端是在安装 Node.js 之前就已经打开的，请完全关闭并重新打开后再试；\n"
            "3. 如果全新终端里 node -v 也报错，请重新安装 Node.js 并确认勾选 "
            "\"Add to PATH\" 选项：https://nodejs.org"
        )
    return [node_exe, script_name]

def _find_free_port(preferred: int = 8765) -> int:
    """优先使用 preferred 端口，若被占用则自动换一个空闲端口。"""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("未能找到可用的本地端口用于 WhatsApp 桥接服务")


class WhatsAppBridgeStartupError(Exception):
    """Node.js 桥接进程启动失败（通常是 Node.js 未安装或依赖未安装）。"""


class WhatsAppBridgeClient:
    """与 whatsapp_service.WhatsAppSender 接口一致的桥接客户端。"""

    def __init__(self, chrome_profile_dir: Path, headless: bool = True, dry_run: bool = True,
                 bridge_project_dir: Optional[Path] = None):
        # 参数名保留 chrome_profile_dir 以兼容旧调用方，此处用作 session 持久化目录
        self.session_dir = Path(chrome_profile_dir)
        self.headless = headless
        self.dry_run = dry_run
        self.bridge_dir = bridge_project_dir or (Path(__file__).resolve().parent / BRIDGE_DIR_NAME)
        self.port: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None

    # ---------------------------------------------------------------- 生命周期
    def _forward_bridge_output(self) -> None:
        """将 Node.js Bridge 的标准输出写入 Python 应用日志。"""
        if not self.process or not self.process.stdout:
            return

        try:
            for raw_line in iter(self.process.stdout.readline, ""):
                line = raw_line.rstrip()

                if line:
                    logger.info("[Bridge] %s", line)

        except Exception as exc:
            logger.warning("读取 WhatsApp Bridge 输出失败：%s", exc)
    def start_browser(self) -> None:
        """启动 Node.js 桥接进程（相当于原来的“打开浏览器”动作）。"""
        if self.dry_run:
            logger.info("演示模式：跳过 WhatsApp 桥接服务启动")
            return
        if self.process and self.process.poll() is None:
            logger.info("WhatsApp 桥接服务已在运行，跳过重复启动")
            return

        if not self.bridge_dir.exists():
            raise WhatsAppBridgeStartupError(
                f"未找到桥接服务目录：{self.bridge_dir}，请确认 whatsapp_bridge 文件夹与本程序放在一起。"
            )
        if not (self.bridge_dir / "node_modules").exists():
            raise WhatsAppBridgeStartupError(
                "未检测到 node_modules，请先在 whatsapp_bridge 目录下运行：npm install"
            )

        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.port = _find_free_port()

        env = {
            "PORT": str(self.port),
            "SESSION_PATH": str(self.session_dir),
            "HEADLESS": "true" if self.headless else "false",
        }
        import os
        full_env = {**os.environ, **env}

        try:
            popen_args = build_node_popen_args(self.bridge_dir)
            self.process = subprocess.Popen(
                popen_args,
                cwd=str(self.bridge_dir),
                env=full_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            threading.Thread(
                target=self._forward_bridge_output,
                daemon=True,
                name="WhatsAppBridgeOutput",
            ).start()
        except FileNotFoundError as exc:
            raise WhatsAppBridgeStartupError(str(exc)) from exc

        self._wait_for_bridge_ready()

    def _wait_for_bridge_ready(self) -> None:
        deadline = time.time() + STARTUP_TIMEOUT_SECONDS
        url = f"http://127.0.0.1:{self.port}/status"
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                raise WhatsAppBridgeStartupError(
                    "WhatsApp 桥接进程异常退出，请检查是否已执行 npm install，或查看程序日志。"
                )
            try:
                resp = requests.get(url, timeout=2)
                if resp.ok:
                    logger.info("WhatsApp 桥接服务已就绪，端口: %s", self.port)
                    return
            except requests.RequestException:
                pass
            time.sleep(1)
        raise WhatsAppBridgeStartupError("WhatsApp 桥接服务启动超时，请检查 Node.js 环境是否正常。")

    # ---------------------------------------------------------------- 状态查询
    def _get_and_log_bridge_status(self, timeout: int = 5) -> Optional[dict]:
        """调用 /status，并将 Bridge 的原始回传内容写入日志。"""
        if not self.port:
            logger.warning("Bridge 状态查询跳过：端口尚未分配，Bridge 未启动")
            return None

        url = f"http://127.0.0.1:{self.port}/status"

        try:
            resp = requests.get(url, timeout=timeout)

            try:
                payload = resp.json()
            except ValueError:
                logger.warning(
                    "Bridge 状态回传不是合法 JSON：HTTP %s，body=%r",
                    resp.status_code,
                    resp.text[:1000],
                )
                return None

            logger.info(
                "Bridge 状态回传：HTTP %s，payload=%s",
                resp.status_code,
                payload,
            )
            return payload

        except requests.RequestException as exc:
            logger.warning(
                "Bridge 状态查询失败：url=%s，error=%s",
                url,
                exc,
            )
            return None
    def is_logged_in(self, timeout: int = 15) -> bool:
        if self.dry_run:
            return True

        payload = self._get_and_log_bridge_status(timeout=timeout)
        return bool(payload and payload.get("status") == "READY")
    def is_browser_alive(self) -> bool:
        if self.dry_run:
            return True
        return bool(self.process and self.process.poll() is None)

    def get_login_qr_data_url(self) -> Optional[str]:
        """获取当前登录二维码（base64 data URL），供 GUI 内嵌显示，无需单独打开浏览器窗口。"""
        if self.dry_run or not self.port:
            return None
        try:
            resp = requests.get(f"http://127.0.0.1:{self.port}/qr", timeout=5)
            if resp.ok:
                return resp.json().get("qr_data_url")
        except requests.RequestException:
            pass
        return None

    def search_groups(self, keyword: str = "") -> list:
        """按关键字搜索 WhatsApp 群组，返回 [{'id': ..., 'name': ...}, ...]。
        keyword 为空字符串时返回全部群组列表。"""
        if self.dry_run:
            logger.info("演示模式：返回模拟群组列表")
            return [{"id": "000000000-0000000000@g.us", "name": f"模拟群组（关键字：{keyword or '全部'}）"}]
        if not self.is_logged_in():
            logger.error("尚未登录 WhatsApp，无法搜索群组")
            return []
        try:
            resp = requests.get(
                f"http://127.0.0.1:{self.port}/search_groups",
                params={"keyword": keyword},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if resp.ok:
                return resp.json().get("groups", [])
            logger.error("搜索群组失败：HTTP %s", resp.status_code)
            return []
        except requests.RequestException as exc:
            logger.error("搜索群组时出错：%s", exc)
            return []

    def list_groups(self, keyword: str = "") -> list:
        """获取全部群组，keyword 非空时按群组名称粗略过滤。"""
        if self.dry_run:
            demo_groups = [
                {"id": "111111111-1111111111@g.us", "name": "工程告警群"},
                {"id": "222222222-2222222222@g.us", "name": "运维通知群"},
                {"id": "333333333-3333333333@g.us", "name": "项目工程讨论组"},
            ]

            keyword_lower = keyword.lower().strip()

            return [
                group
                for group in demo_groups
                if not keyword_lower or keyword_lower in group["name"].lower()
            ]

        if not self.is_logged_in():
            logger.error("尚未登录 WhatsApp，无法获取群组列表")
            return []

        try:
            response = requests.get(
                f"http://127.0.0.1:{self.port}/list_groups",
                params={"keyword": keyword},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if response.ok:
                return response.json().get("groups", [])

            try:
                detail = response.json().get("error", response.text)
            except Exception:
                detail = response.text

            logger.error(
                "获取群组列表失败：HTTP %s，详情：%s",
                response.status_code,
                detail,
            )
            return []

        except requests.RequestException as exc:
            logger.error("获取群组列表时出错：%s", exc)
            return []
    def verify_group_exists(self, group_exact_name: str) -> bool:
        if self.dry_run:
            logger.info("演示模式：假定群组 %s 存在", group_exact_name)
            return True
        if not self.is_logged_in():
            return False
        try:
            resp = requests.get(
                f"http://127.0.0.1:{self.port}/verify_group",
                params={"name": group_exact_name}, timeout=REQUEST_TIMEOUT_SECONDS,
            )
            return resp.ok and resp.json().get("found", False)
        except requests.RequestException as exc:
            logger.error("查询群组是否存在时出错：%s", exc)
            return False

    # ---------------------------------------------------------------- 发送
    def send_text_and_image(self, group_exact_name: str, text: str,
                              image_path: Optional[str] = None) -> dict:
        if self.dry_run:
            logger.info("演示模式：模拟发送到群组 [%s]，图片=%s（内容不写入日志）",
                        group_exact_name, bool(image_path))
            return {"status": "SENT", "detail": "dry_run"}

        if not self.is_browser_alive():
            return {"status": "FAILED", "detail": "WhatsApp 桥接服务未启动，请先点击“登录 WhatsApp Web”"}
        if not self.is_logged_in():
            return {"status": "FAILED", "detail": "WhatsApp 尚未登录，请扫码登录后重试"}

        try:
            resp = requests.post(
                f"http://127.0.0.1:{self.port}/send",
                json={"group_name": group_exact_name, "text": text, "image_path": image_path},
                timeout=60,
            )
            if resp.ok:
                return resp.json()
            return {"status": "FAILED", "detail": f"桥接服务返回错误：HTTP {resp.status_code}"}
        except requests.RequestException as exc:
            return {"status": "FAILED", "detail": f"调用桥接服务失败：{exc}"}

    def quit(self) -> None:
        """优雅关闭 Node.js 桥接进程。"""
        if self.dry_run or not self.process:
            return
        try:
            if self.port:
                requests.post(f"http://127.0.0.1:{self.port}/shutdown", timeout=5)
        except requests.RequestException:
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception as exc:
            logger.warning("关闭 WhatsApp 桥接进程时出现异常：%s", exc)
        finally:
            self.process = None
