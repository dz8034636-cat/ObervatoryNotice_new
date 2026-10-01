"""whatsapp_service.py —— 告警卡片图片生成 + WhatsApp Web Selenium 发送 合并模块。

合并自：services/image_generator.py, services/whatsapp_sender.py
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core_new import NormalizedWarning, get_logger, get_generated_images_dir
from hko_data import CATEGORY_TITLE, EVENT_DISPLAY, _fmt_time, is_urgent

logger = get_logger("whatsapp_service")

# ============================================================
# 1. 告警卡片图片生成（Pillow 本地渲染，不依赖网页截图/外部图片链接）
# ============================================================
CARD_WIDTH = 900
CARD_HEIGHT = 560
PROJECT_DIR = Path(__file__).resolve().parent
WARNING_ICON_DIR = PROJECT_DIR / "assets" / "warning_icons"

DEFAULT_STYLE = {
    "icon": None,
    "background": (245, 247, 250),
    "accent": (0, 102, 179),
    "text": (30, 30, 30),
    "source": "资料来源：香港天文台（HKO）",
}

WARNING_CARD_STYLE = {
    # --------------------------------------------------------
    # 香港劳工处：工作暑热警告
    # --------------------------------------------------------
    "HSWW_AMBER": {
        "icon": "hsww_amber.png",
        "background": (255, 248, 220),
        "accent": (210, 145, 0),
        "text": (45, 35, 0),
        "source": "资料来源：香港劳工处工作暑热警告",
    },
    "HSWW_RED": {
        "icon": "hsww_red.png",
        "background": (95, 15, 15),
        "accent": (220, 40, 40),
        "text": (255, 255, 255),
        "source": "资料来源：香港劳工处工作暑热警告",
    },
    "HSWW_BLACK": {
        "icon": "hsww_black.png",
        "background": (24, 24, 24),
        "accent": (95, 95, 95),
        "text": (255, 255, 255),
        "source": "资料来源：香港劳工处工作暑热警告",
    },

    # --------------------------------------------------------
    # 香港天文台：暴雨警告
    # --------------------------------------------------------
    "RAIN_YELLOW": {
        "icon": "rain_yellow.png",
        "background": (255, 248, 220),
        "accent": (210, 145, 0),
        "text": (45, 35, 0),
        "source": "资料来源：香港天文台（HKO）",
    },
    "RAIN_RED": {
        "icon": "rain_red.png",
        "background": (95, 15, 15),
        "accent": (220, 40, 40),
        "text": (255, 255, 255),
        "source": "资料来源：香港天文台（HKO）",
    },
    "RAIN_BLACK": {
        "icon": "rain_black.png",
        "background": (24, 24, 24),
        "accent": (95, 95, 95),
        "text": (255, 255, 255),
        "source": "资料来源：香港天文台（HKO）",
    },

    # --------------------------------------------------------
    # 香港天文台：热带气旋警告
    # --------------------------------------------------------
    "TC3": {
        "icon": "tc3.png",
        "background": (240, 247, 255),
        "accent": (0, 102, 179),
        "text": (20, 35, 60),
        "source": "资料来源：香港天文台（HKO）",
    },
    "TC8NE": {
        "icon": "tc8ne.png",
        "background": (95, 15, 15),
        "accent": (220, 40, 40),
        "text": (255, 255, 255),
        "source": "资料来源：香港天文台（HKO）",
    },
    "TC8SE": {
        "icon": "tc8se.png",
        "background": (95, 15, 15),
        "accent": (220, 40, 40),
        "text": (255, 255, 255),
        "source": "资料来源：香港天文台（HKO）",
    },
    "TC8SW": {
        "icon": "tc8sw.png",
        "background": (95, 15, 15),
        "accent": (220, 40, 40),
        "text": (255, 255, 255),
        "source": "资料来源：香港天文台（HKO）",
    },
    "TC8NW": {
        "icon": "tc8nw.png",
        "background": (95, 15, 15),
        "accent": (220, 40, 40),
        "text": (255, 255, 255),
        "source": "资料来源：香港天文台（HKO）",
    },
    "TC10": {
        "icon": "tc10.png",
        "background": (55, 5, 5),
        "accent": (230, 35, 35),
        "text": (255, 255, 255),
        "source": "资料来源：香港天文台（HKO）",
    },
    "HOT_WEATHER": {
    "icon": "hot_weather.png",
    "background": (255, 247, 231),
    "accent": (196, 106, 19),
    "text": (55, 39, 18),
    "source": "資料來源：香港天文台（HKO）",
},
}
NORMAL_BG, NORMAL_ACCENT = (245, 247, 250), (0, 102, 179)
URGENT_BG, URGENT_ACCENT = (60, 8, 8), (220, 30, 30)

CANDIDATE_FONT_PATHS = [
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in CANDIDATE_FONT_PATHS:
        p = Path(path)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    logger.warning("未找到系统中文字体，使用 Pillow 默认字体（中文可能显示为方块）")
    return ImageFont.load_default()


def get_warning_card_style(event: NormalizedWarning) -> dict:
    """按 warning_level 取得图标、颜色、来源；找不到时使用默认样式。"""
    return {
        **DEFAULT_STYLE,
        **WARNING_CARD_STYLE.get(event.warning_level, {}),
    }


def load_warning_icon(event: NormalizedWarning) -> Optional[Image.Image]:
    """读取对应官方警告标志；缺图时只生成文字卡片，不中断告警流程。"""
    style = get_warning_card_style(event)
    icon_name = style["icon"]

    if not icon_name:
        return None

    icon_path = WARNING_ICON_DIR / icon_name

    if not icon_path.exists():
        logger.warning(
            "找不到警告图标：level=%s, path=%s",
            event.warning_level,
            icon_path,
        )
        return None

    try:
        return Image.open(icon_path).convert("RGBA")
    except Exception as exc:
        logger.warning(
            "读取警告图标失败：%s，原因：%s",
            icon_path,
            exc,
        )
        return None

def paste_warning_icon(
    base_image: Image.Image,
    event: NormalizedWarning,
) -> bool:
    """把官方警告图标贴到卡片右上角；失败不影响卡片生成。"""
    icon = load_warning_icon(event)

    if icon is None:
        return False

    try:
        panel_size = 220
        icon.thumbnail((190, 190), Image.Resampling.LANCZOS)

        panel = Image.new(
            "RGBA",
            (panel_size, panel_size),
            (255, 255, 255, 235),
        )

        x = (panel_size - icon.width) // 2
        y = (panel_size - icon.height) // 2
        panel.alpha_composite(icon, (x, y))

        image_rgba = base_image.convert("RGBA")
        image_rgba.alpha_composite(
            panel,
            (CARD_WIDTH - panel_size - 40, 45),
        )

        base_image.paste(image_rgba.convert("RGB"))
        return True

    except Exception as exc:
        logger.warning(
            "绘制警告图标失败：level=%s, error=%s",
            event.warning_level,
            exc,
        )
        return False

WARNING_MESSAGE_FORMATS = {
    "HOT_WEATHER": (  "天文台酷熱天氣警告", "酷熱天氣警告","現正生效","請各工隊注意防暑降溫，並安排適當休息及補充水分。","天文台"),
    "HSWW_AMBER": ("勞工處工作暑熱警告", "黃色工作暑熱警告", "現正生效","表示部分工作環境下的熱壓力頗高，請採取適當的防暑措施。",  "勞工處"),
    "HSWW_RED": ("勞工處工作暑熱警告", "紅色工作暑熱警告", "現正生效","表示部分工作環境下的熱壓力甚高，請採取適當的防暑措施。", "勞工處"),
    "HSWW_BLACK": ("勞工處工作暑熱警告", "黑色工作暑熱警告", "現正生效","表示部分工作環境下的熱壓力極高，請採取適當的防暑措施。", "勞工處"),
    "RAIN_YELLOW": ("天文台暴雨警告", "黃色暴雨警告", "現正生效", "請各工隊注意安全駕駛及戶外工作安全", "天文台"),
    "RAIN_RED": ("天文台暴雨警告", "紅色暴雨警告", "現正生效", "請各工隊注意安全駕駛及戶外工作安全", "天文台"),
    "RAIN_BLACK": ("天文台暴雨警告", "黑色暴雨警告", "現正生效", "請各工隊注意安全駕駛及暫停所有戶外工作，在室內暫避", "天文台"),
    "TC3": ("天文台熱帶氣旋警報", "3號強風信號", "現正生效", "請各工隊注意安全駕駛及戶外工作安全，收起鬆散物料。", "天文台"),
    "TC8NE": ("天文台熱帶氣旋警報", "8號烈風或暴風信號", "現正生效", "請各工隊注意安全駕駛及暫停戶外工作，在室內安全地方暫避。", "天文台"),
    "TC8SE": ("天文台熱帶氣旋警報", "8號烈風或暴風信號", "現正生效", "請各工隊注意安全駕駛及暫停戶外工作，在室內安全地方暫避。", "天文台"),
    "TC8SW": ("天文台熱帶氣旋警報", "8號烈風或暴風信號", "現正生效", "請各工隊注意安全駕駛及暫停戶外工作，在室內安全地方暫避。", "天文台"),
    "TC8NW": ("天文台熱帶氣旋警報", "8號烈風或暴風信號", "現正生效", "請各工隊注意安全駕駛及暫停戶外工作，在室內安全地方暫避。", "天文台"),
    "TC9": ("天文台熱帶氣旋警報", "9號烈風或暴風風力增強信號", "現正生效", "請各工隊注意安全駕駛及暫停戶外工作，在室內安全地方暫避。", "天文台"),
    "TC10": ("天文台熱帶氣旋警報", "10號颶風信號", "現正生效", "請各工隊注意安全駕駛及暫停戶外工作，在室內安全地方暫避。", "天文台"),
}

CANCEL_EVENT_TYPES = {"CANCEL", "CANCELLED", "CANCELLATION"}


def _warning_content(event: NormalizedWarning) -> tuple[str, str, str, Optional[str], str]:
    """取得指定警告等級的繁體中文發送文字；未知等級保留可讀的安全回退。"""
    fallback_source = "勞工處及天文台" if event.warning_level.startswith("HSWW_") else "天文台"
    return WARNING_MESSAGE_FORMATS.get(
        event.warning_level,
        ("天文台天氣警告", event.display_name(), "現正生效", None, fallback_source),
    )


def build_warning_message(event: NormalizedWarning) -> str:
    """建立 WhatsApp 純文字訊息。

    新發佈、更新及降級均採用既定版式；取消訊息不顯示任何時間。
    """
    title, level, active_status, instruction, source = _warning_content(event)
    is_cancelled = event.event_type.upper() in CANCEL_EVENT_TYPES
    status = "已取消" if is_cancelled else active_status
    lines = [title, "", level, "", f"狀態：{status}"]

    if not is_cancelled:
        # 更新或降級事件優先使用其更新時間；其餘使用發佈時間。
        event_time = event.update_time or event.issue_time
        lines.extend(["", f"生效時間：{_fmt_time(event_time)}"])
        if instruction:
            lines.extend(["", instruction])

    lines.extend(["", f"資料來源：{source}"])
    return "\n".join(lines)


def generate_alert_card(
    event: NormalizedWarning,
    org_name: str = "",
    app_name: str = "香港天文台告警通知工具",
    footer_note: str = "",
) -> str:
    """生成精簡 PNG 卡片：僅保留發佈機構、狀態、發佈時間和警告標志。"""
    style = get_warning_card_style(event)
    bg = style["background"]
    accent = style["accent"]
    text_color = style.get("text", (30, 30, 30))
    is_labour_warning = (
        event.warning_category == "heat_stress_work"
        or event.warning_level.startswith("HSWW_")
    )
    publisher = "勞工處" if is_labour_warning else "香港天文台"
    is_cancelled = event.event_type.upper() in CANCEL_EVENT_TYPES
    status = "已取消" if is_cancelled else "現正生效"
    published_time = event.update_time or event.issue_time
    title, warning_content, _, _, _ = _warning_content(event)

    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), color=bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, CARD_WIDTH, 14], fill=accent)
    draw.rectangle([0, CARD_HEIGHT - 14, CARD_WIDTH, CARD_HEIGHT], fill=accent)

    label_font = _load_font(28)
    value_font = _load_font(34)
    draw.text((45, 45), "發佈機構", font=label_font, fill=accent)
    draw.text((45, 82), publisher, font=value_font, fill=text_color)

    draw.text((45, 150), "警告內容", font=label_font, fill=accent)
    draw.text((45, 187), title, font=label_font, fill=text_color)
    draw.text((45, 223), warning_content, font=value_font, fill=text_color)

    draw.text((45, 295), "狀態", font=label_font, fill=accent)
    draw.text((45, 332), status, font=value_font, fill=text_color)

    if not is_cancelled:
        draw.text((45, 400), "發佈時間", font=label_font, fill=accent)
        draw.text(
            (45, 437),
            _fmt_time(published_time),
            font=label_font,
            fill=text_color,
        )

    paste_warning_icon(img, event)

    out_path = get_generated_images_dir() / (
        f"alert_{event.warning_category}_{event.warning_level}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    )
    img.save(out_path, format="PNG")
    logger.info("已生成精簡告警卡片图片：%s（等级=%s）", out_path, event.warning_level)
    return str(out_path)

# ============================================================
# 2. WhatsApp Web Selenium 发送层
# ============================================================
WHATSAPP_WEB_URL = "https://web.whatsapp.com"

# 维护提示：WhatsApp Web 的页面 DOM 结构可能随时变化。发送失败时优先检查此处的
# 选择器是否需要更新（可通过浏览器开发者工具 F12 重新抓取）。
SELECTORS = {
    "chat_list_pane": "div[aria-label='Chat list'], div[id='pane-side']",
    "search_box": "div[contenteditable='true'][data-tab='3'], div[contenteditable='true'][aria-label='Search input textbox']",
    "chat_title_result": "span[title]",
    "message_input_box": "div[contenteditable='true'][data-tab='10'], footer div[contenteditable='true']",
    "attach_button": "div[title='Attach'], button[title='Attach']",
    "image_input_file": "input[accept*='image']",
    "send_button": "button[aria-label='Send'], span[data-icon='send']",
}

MAX_SEND_RETRY = 3
RETRY_BACKOFF_SECONDS = 5


class WhatsAppNotReadyError(Exception):
    pass


class WhatsAppGroupNotFoundError(Exception):
    pass


class WhatsAppSender:
    """WhatsApp Web 自动化发送器：复用持久化 Chrome Profile，不绕过登录/验证机制。"""

    def __init__(self, chrome_profile_dir: Path, headless: bool = False, dry_run: bool = True):
        self.chrome_profile_dir = Path(chrome_profile_dir)
        self.headless = headless
        self.dry_run = dry_run
        self.driver: Optional[webdriver.Chrome] = None

    def start_browser(self) -> None:
        if self.dry_run:
            logger.info("演示模式：跳过真实浏览器启动")
            return
        self.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        options = Options()
        options.add_argument(f"--user-data-dir={self.chrome_profile_dir}")
        options.add_argument("--profile-directory=Default")
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--start-maximized")
        try:
            self.driver = webdriver.Chrome(options=options)  # Selenium Manager 自动管理驱动
        except WebDriverException as exc:
            logger.error("启动 Chrome 失败：%s", exc)
            raise RuntimeError(
                "无法启动 Chrome 浏览器。请确认已安装 Google Chrome，且未被安全软件拦截 "
                "ChromeDriver 的自动下载/运行。"
            ) from exc
        self.driver.get(WHATSAPP_WEB_URL)

    def is_logged_in(self, timeout: int = 15) -> bool:
        if self.dry_run:
            return True
        if not self.driver:
            return False
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["chat_list_pane"]))
            )
            return True
        except TimeoutException:
            return False

    def is_browser_alive(self) -> bool:
        if self.dry_run:
            return True
        if not self.driver:
            return False
        try:
            _ = self.driver.title
            return True
        except WebDriverException:
            return False

    def _find_and_open_group(self, group_exact_name: str, timeout: int = 15) -> None:
        try:
            search_box = WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, SELECTORS["search_box"]))
            )
            search_box.click()
            search_box.send_keys(Keys.CONTROL, "a")
            search_box.send_keys(group_exact_name)
            time.sleep(2)
            results = self.driver.find_elements(By.CSS_SELECTOR, SELECTORS["chat_title_result"])
            target = None
            for r in results:
                title_attr = r.get_attribute("title") or r.text
                if title_attr and title_attr.strip() == group_exact_name.strip():
                    target = r
                    break
            if not target:
                raise WhatsAppGroupNotFoundError(f"未能在 WhatsApp 中找到名称完全匹配的群组：{group_exact_name}")
            target.click()
            time.sleep(1)
        except (TimeoutException, NoSuchElementException) as exc:
            raise WhatsAppGroupNotFoundError(f"查找群组 {group_exact_name} 时出错：{exc}") from exc

    def verify_group_exists(self, group_exact_name: str) -> bool:
        if self.dry_run:
            logger.info("演示模式：假定群组 %s 存在", group_exact_name)
            return True
        if not self.is_logged_in():
            raise WhatsAppNotReadyError("WhatsApp Web 尚未登录，无法测试群组")
        try:
            self._find_and_open_group(group_exact_name)
            return True
        except WhatsAppGroupNotFoundError:
            return False

    def list_groups(self) -> list:
        """Selenium 版本暂不支持自动枚举全部群组 ID，请改用 whatsapp_bridge_client.WhatsAppBridgeClient。"""
        logger.warning("当前使用 Selenium 版本发送器，暂不支持自动显示 Group ID 列表")
        return []
    def search_groups(self, keyword: str = "") -> list:
        """Selenium 版本暂不支持通过 DOM 稳定获取真实 Group ID（JID），
        如需该功能请改用 whatsapp_bridge_client.WhatsAppBridgeClient。"""
        logger.warning("当前使用 Selenium 版本发送器，暂不支持“搜索 Group ID”功能")
        return []
    def send_text_and_image(self, group_exact_name: str, text: str, image_path: Optional[str] = None) -> dict:
        """始终优先发送文字；图片发送失败不影响文字发送结果。返回 {status, detail}。"""
        if self.dry_run:
            logger.info("演示模式：模拟发送到群组 [%s]，图片=%s（内容不写入日志）", group_exact_name, bool(image_path))
            return {"status": "SENT", "detail": "dry_run"}

        if not self.driver or not self.is_browser_alive():
            return {"status": "FAILED", "detail": "浏览器未启动或已关闭，请重新打开并登录"}
        if not self.is_logged_in():
            return {"status": "FAILED", "detail": "WhatsApp Web 未登录，请重新扫码登录"}

        last_error = ""
        for attempt in range(1, MAX_SEND_RETRY + 1):
            try:
                self._find_and_open_group(group_exact_name)
                self._send_text(text)
                image_ok, image_error = True, ""
                if image_path:
                    try:
                        self._send_image(image_path)
                    except Exception as exc:
                        image_ok, image_error = False, str(exc)
                        logger.error("图片发送失败，已降级为仅发送文字：%s", exc)
                return {"status": "SENT" if image_ok else "SENT_TEXT_ONLY", "detail": image_error}
            except WhatsAppGroupNotFoundError as exc:
                return {"status": "FAILED", "detail": str(exc)}
            except Exception as exc:
                last_error = str(exc)
                logger.warning("发送第 %d 次尝试失败：%s", attempt, exc)
                time.sleep(RETRY_BACKOFF_SECONDS)
        return {"status": "FAILED", "detail": f"重试 {MAX_SEND_RETRY} 次后仍失败：{last_error}"}

    def _send_text(self, text: str) -> None:
        box = WebDriverWait(self.driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SELECTORS["message_input_box"]))
        )
        box.click()
        for line in text.split("\n"):
            box.send_keys(line)
            box.send_keys(Keys.SHIFT, Keys.ENTER)
        box.send_keys(Keys.ENTER)
        time.sleep(1)

    def _send_image(self, image_path: str) -> None:
        attach_btn = WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SELECTORS["attach_button"]))
        )
        attach_btn.click()
        file_input = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["image_input_file"]))
        )
        file_input.send_keys(str(Path(image_path).resolve()))
        time.sleep(2)
        send_btn = WebDriverWait(self.driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SELECTORS["send_button"]))
        )
        send_btn.click()
        time.sleep(1)

    def quit(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except Exception as exc:
                logger.warning("关闭浏览器时出现异常：%s", exc)
            finally:
                self.driver = None
