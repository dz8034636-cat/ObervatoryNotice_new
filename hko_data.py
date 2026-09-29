"""hko_data.py —— HKO 数据获取 / 标准化 / 状态机 / 工作时间规则 / 消息文本 合并模块。

合并自：services/hko_client.py, services/warning_normalizer.py,
        services/state_machine.py, services/business_hours.py,
        services/subscription_filter.py, services/message_builder.py

============================================================
mcp-hko 可行性核验结论（2026-08 核实，写入代码注释供维护者查阅）：
============================================================
1. mcp-hko（github: louiscklaw/mcp-hko）是用 TypeScript/Node.js 编写的
   MCP Server，通过 stdio JSON-RPC（FastMCP 框架）与 LLM 客户端通信，
   设计目标是被 AI Agent 以“工具调用”形式使用，而非被普通 Python 桌面
   GUI 程序作为常规业务数据 API 直接调用。
2. 若要在 Python GUI 中调用，需额外引入 Node.js 运行时、实现 MCP stdio
   客户端、管理子进程生命周期、并把“对话式”结果再解析为结构化字段——
   对面向非 IT 运维人员、要求“简单可靠、开机即用”的 Windows 工具而言
   复杂度过高，且引入额外单点故障。
3. mcp-hko 内部对 warnsum 的实现本质仍是转发调用 HKO 官方 Open Data API，
   并未提供比官方 API 更丰富的字段，也没有官方图片 URL。
4. 结论：mcp-hko 不适合作为本地 GUI 的稳定直接数据源。本程序默认使用
   HKO 官方 Open Data API（唯一可靠数据源）；data_source_mode=mcp_hko
   仅作实验性预留，检测到不可用会自动降级为 hko_open_data_api 并记录日志。
============================================================
"""
from __future__ import annotations

import hashlib
import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, time as dtime
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

import core_new
from core_new import (CAT_HEAT_STRESS_WORK,
HSWW_NONE,
HSWW_AMBER,
HSWW_RED,
HSWW_BLACK,
    CAT_HOT_WEATHER, CAT_RAINSTORM, CAT_TROPICAL_CYCLONE, EVENT_CANCEL,
    EVENT_DOWNGRADE, EVENT_ISSUE, EVENT_UPDATE, EVENT_UPGRADE,
    GroupConfig, HIGH_PRIORITY_LEVELS, HOT_NONE, HOT_WEATHER,
    NormalizedWarning, RAIN_BLACK, RAIN_NONE, RAIN_RED, RAIN_YELLOW,
    TC_NONE, get_logger, get_snapshots_dir, level_display_name,
)

logger = get_logger("hko_data")

# ============================================================
# 1. HKO 数据源抽象层
# ============================================================
OPEN_DATA_BASE_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
LABOUR_HSWW_API_URL = (
    "https://data.weather.gov.hk/weatherAPI/opendata/hsww.php"
)
EVENT_RESEND_WORK_HOURS = "RESEND_WORK_HOURS"
HSWW_LEVEL_MAP = {
    "AMBER": HSWW_AMBER,
    "RED": HSWW_RED,
    "BLACK": HSWW_BLACK,
}
REQUEST_TIMEOUT_SECONDS = 10


class HKODataSource(ABC):
    @abstractmethod
    def fetch_warnsum(self, lang: str = "sc") -> dict:
        ...


class HKOOpenDataAPISource(HKODataSource):
    """通过 HKO 官方 Open Data API 获取 warnsum（默认、可靠的数据源）。"""

    def fetch_warnsum(self, lang: str = "sc") -> dict:
        params = {"dataType": "warnsum", "lang": lang}
        resp = requests.get(OPEN_DATA_BASE_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("HKO Open Data API 返回格式异常，期望 JSON 对象")
        return data

def fetch_hsww_warning(lang: str = "tc") -> dict:
        """
        读取香港劳工处工作暑热警告（HSWW）官方 API。

        API 在没有有效警告（或取消超过 60 分钟）时，可能返回空 JSON、
        空对象或不含 warningLevel 的 JSON。
        """
        response = requests.get(
            LABOUR_HSWW_API_URL,
            params={"lang": lang},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, dict):
            raise ValueError("劳工处 HSWW API 返回的不是 JSON object")

        return data


class McpHkoDataSource(HKODataSource):
    """mcp-hko 实验性数据源：仅做环境探测，不实现完整 MCP stdio 协议（见文件头核验结论）。"""

    def is_environment_available(self) -> bool:
        return shutil.which("node") is not None and shutil.which("npx") is not None

    def fetch_warnsum(self, lang: str = "sc") -> dict:
        if not self.is_environment_available():
            raise RuntimeError("本机未检测到 Node.js/npx 环境，mcp-hko 不适合直接用于本 GUI 程序。")
        raise NotImplementedError(
            "mcp-hko 使用 stdio JSON-RPC 协议，为 LLM Agent 工具调用设计，不适合被本 "
            "Python GUI 直接同步调用，已在架构核验阶段判定弃用，请使用 hko_open_data_api。"
        )


class WarnsumSnapshot:
    def __init__(self, raw: dict, fetched_at: str, payload_hash: str, snapshot_path: str, source_mode: str):
        self.raw = raw
        self.fetched_at = fetched_at
        self.payload_hash = payload_hash
        self.snapshot_path = snapshot_path
        self.source_mode = source_mode


class HKOClient:
    """统一客户端：按配置选择数据源，自动降级，保存原始 JSON 快照。"""

    def __init__(self, data_source_mode: str = "auto"):
        self.data_source_mode = data_source_mode
        self._open_api = HKOOpenDataAPISource()
        self._mcp = McpHkoDataSource()
        self.active_source_name = "hko_open_data_api"

    def _resolve_source(self) -> HKODataSource:
        if self.data_source_mode == "hko_open_data_api":
            self.active_source_name = "hko_open_data_api"
            return self._open_api
        if self.data_source_mode == "mcp_hko":
            if self._mcp.is_environment_available():
                self.active_source_name = "mcp_hko"
                return self._mcp
            logger.warning("mcp_hko 模式已选择，但环境不可用，自动降级为 hko_open_data_api")
            self.active_source_name = "hko_open_data_api"
            return self._open_api
        if self._mcp.is_environment_available():
            try:
                self._mcp.fetch_warnsum()
                self.active_source_name = "mcp_hko"
                return self._mcp
            except Exception as exc:
                logger.info("mcp_hko 不适用（%s），自动使用 hko_open_data_api", exc)
        self.active_source_name = "hko_open_data_api"
        return self._open_api

    def fetch_hsww_snapshot(self, lang: str = "tc") -> dict:
        """获取劳工处工作暑热警告原始 JSON。"""
        return fetch_hsww_warning(lang=lang)
    def fetch_warnsum_snapshot(self, lang: str = "sc") -> WarnsumSnapshot:
        source = self._resolve_source()
        try:
            raw = source.fetch_warnsum(lang=lang)
        except Exception as exc:
            if self.active_source_name != "hko_open_data_api":
                logger.warning("数据源 %s 调用失败（%s），改用官方 Open Data API 重试", self.active_source_name, exc)
                raw = self._open_api.fetch_warnsum(lang=lang)
                self.active_source_name = "hko_open_data_api"
            else:
                raise

        fetched_at = datetime.now().isoformat(timespec="seconds")
        payload_str = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        snapshot_path = get_snapshots_dir() / f"warnsum_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            snapshot_path.write_text(payload_str, encoding="utf-8")
        except Exception as exc:
            logger.error("保存 warnsum 快照失败: %s", exc)
        return WarnsumSnapshot(raw, fetched_at, payload_hash, str(snapshot_path), self.active_source_name)


# ============================================================
# 2. 标准化层
# ============================================================
TC_CODE_MAP = {
    "TC3": "TC3", "TC8NE": "TC8NE", "TC8SE": "TC8SE", "TC8SW": "TC8SW",
    "TC8NW": "TC8NW", "TC9": "TC9", "TC10": "TC10",
    "TC1": "TC_NONE",  # 一号戒备信号不在本项目通知范围内
}
RAIN_CODE_MAP = {"WRAINA": RAIN_YELLOW, "WRAINR": RAIN_RED, "WRAINB": RAIN_BLACK}
# 预留：未来可启用的其他告警类别代码映射（本版本默认不启用）
EXTENDED_CATEGORY_CODE_MAP = {
    "WTS": "thunderstorm", "WL": "landslip", "WFNTSA": "flooding",
    "WMSGNL": "monsoon", "WFIRE": "fire", "WFROST": "frost", "WCOLD": "cold",
}


def normalize_warnsum(raw: dict, detected_time: Optional[str] = None,
                       payload_hash: str = "") -> Dict[str, NormalizedWarning]:
    """把 warnsum 原始 JSON 转换成 {category: NormalizedWarning} 字典（含无信号状态）。"""
    detected_time = detected_time or datetime.now().isoformat(timespec="seconds")
    result: Dict[str, NormalizedWarning] = {
        CAT_TROPICAL_CYCLONE: NormalizedWarning(CAT_TROPICAL_CYCLONE, TC_NONE, detected_time=detected_time, source_payload_hash=payload_hash),
        CAT_RAINSTORM: NormalizedWarning(CAT_RAINSTORM, RAIN_NONE, detected_time=detected_time, source_payload_hash=payload_hash),
        CAT_HOT_WEATHER: NormalizedWarning(CAT_HOT_WEATHER, HOT_NONE, detected_time=detected_time, source_payload_hash=payload_hash),
    }

    tc_entry = raw.get("WTCSGNL")
    if tc_entry:
        code = tc_entry.get("code", "")
        action = tc_entry.get("actionCode", "")
        if code != "CANCEL" and action != "CANCEL":
            level = TC_CODE_MAP.get(code, TC_NONE)
            result[CAT_TROPICAL_CYCLONE] = NormalizedWarning(
                CAT_TROPICAL_CYCLONE, level, issue_time=tc_entry.get("issueTime"),
                update_time=tc_entry.get("updateTime"), expire_time=tc_entry.get("expireTime"),
                detected_time=detected_time, source_payload_hash=payload_hash, raw_code=code,
            )

    rain_entry = raw.get("WRAIN")

    # 对 rainstorm 单独计算 hash。
    # 不要继续使用整份 warnsum 的 payload_hash，
    # 否则 WTS 雷暴 EXTEND / UPDATE 也会被误判为雨暴资料更新。
    rain_payload = rain_entry if isinstance(rain_entry, dict) else {}
    rain_payload_text = json.dumps(
        rain_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    rain_payload_hash = hashlib.sha256(
        rain_payload_text.encode("utf-8")
    ).hexdigest()

    if isinstance(rain_entry, dict):
        code = str(rain_entry.get("code") or "").upper().strip()
        action = str(rain_entry.get("actionCode") or "").upper().strip()

        issue_time = rain_entry.get("issueTime")
        update_time = rain_entry.get("updateTime")
        expire_time = rain_entry.get("expireTime")
        rain_type = rain_entry.get("type")

        logger.info(
            "HKO 雨暴原始回传："
            "code=%s, actionCode=%s, type=%s, "
            "issueTime=%s, updateTime=%s, expireTime=%s",
            code,
            action,
            rain_type,
            issue_time,
            update_time,
            expire_time,
        )

        # 只有 actionCode=CANCEL 时，当前状态强制变为“无暴雨”。
        #
        # 例如 HKO 可能回传：
        # {
        #   "code": "WRAINA",
        #   "actionCode": "CANCEL",
        #   "updateTime": "2026-08-26T20:40:00+08:00"
        # }
        #
        # WRAINA 表示被取消的是黄色暴雨；
        # actionCode=CANCEL 才表示当前暴雨警告已解除。
        if action == "CANCEL":
            level = RAIN_NONE

            logger.info(
                "HKO 雨暴取消："
                "cancelled_code=%s, cancelled_type=%s, "
                "hko_cancel_time=%s, normalized_level=%s",
                code,
                rain_type,
                update_time,
                level,
            )

        else:
            # 非 CANCEL 时，当前等级由 code 决定：
            # WRAINA -> RAIN_YELLOW
            # WRAINR -> RAIN_RED
            # WRAINB -> RAIN_BLACK
            level = RAIN_CODE_MAP.get(code, RAIN_NONE)

            if level == RAIN_NONE:
                logger.warning(
                    "HKO 雨暴 code 无法识别：code=%s, actionCode=%s；"
                    "保守标准化为 %s",
                    code,
                    action,
                    RAIN_NONE,
                )
            else:
                logger.info(
                    "HKO 雨暴有效状态："
                    "code=%s, actionCode=%s, normalized_level=%s",
                    code,
                    action,
                    level,
                )

        result[CAT_RAINSTORM] = NormalizedWarning(
            warning_category=CAT_RAINSTORM,
            warning_level=level,
            issue_time=issue_time,
            update_time=update_time,
            expire_time=expire_time,
            detected_time=detected_time,
            source_payload_hash=rain_payload_hash,
            raw_code=code,
        )

    else:
        # HKO warnsum 没有 WRAIN，
        # 表示当前没有有效暴雨警告。
        #
        # 保持显式写入，方便日志与状态比较。
        result[CAT_RAINSTORM] = NormalizedWarning(
            warning_category=CAT_RAINSTORM,
            warning_level=RAIN_NONE,
            issue_time=None,
            update_time=None,
            expire_time=None,
            detected_time=detected_time,
            source_payload_hash=rain_payload_hash,
            raw_code=None,
        )

        logger.info(
            "HKO warnsum 没有 WRAIN："
            "雨暴当前状态=%s",
            RAIN_NONE,
        )
    hot_entry = raw.get("WHOT")
    if hot_entry:
        result[CAT_HOT_WEATHER] = NormalizedWarning(
            CAT_HOT_WEATHER, HOT_WEATHER, issue_time=hot_entry.get("issueTime"),
            update_time=hot_entry.get("updateTime"), expire_time=hot_entry.get("expireTime"),
            detected_time=detected_time, source_payload_hash=payload_hash, raw_code=hot_entry.get("code"),
        )

    return result
def normalize_hsww(
    raw: dict,
    detected_time: Optional[str] = None,
) -> NormalizedWarning:
    detected_time = (
        detected_time or datetime.now().isoformat(timespec="seconds")
    )

    # HSWW API 的实际资料在 "hsww" 内，不在 JSON 最外层。
    hsww = raw.get("hsww", {})
    if not isinstance(hsww, dict):
        hsww = {}

    raw_level = str(hsww.get("warningLevel") or "").upper().strip()
    action_code = str(hsww.get("actionCode") or "").upper().strip()

    # 取消状态不可视为当前有效警告。
    if action_code == "CANCEL":
        level = HSWW_NONE
    else:
        level = HSWW_LEVEL_MAP.get(raw_level, HSWW_NONE)

    issue_time = hsww.get("issueTime")
    effective_time = hsww.get("effectiveTime")
    message = hsww.get("desc", "")

    payload_text = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    payload_hash = hashlib.sha256(
        payload_text.encode("utf-8")
    ).hexdigest()

    logger.info(
        "HSWW parsed: level=%s, action=%s, effectiveTime=%s, "
        "issueTime=%s, message=%s",
        raw_level,
        action_code,
        effective_time,
        issue_time,
        message,
    )

    return NormalizedWarning(
        warning_category=CAT_HEAT_STRESS_WORK,
        warning_level=level,
        issue_time=issue_time,
        update_time=effective_time,
        expire_time=None,
        detected_time=detected_time,
        source_payload_hash=payload_hash,
        raw_code=raw_level or action_code or None,
    )

# ============================================================
# 3. 状态机层：比较“本轮”与“上一轮”状态
# ============================================================
def diff_state(previous: Optional[NormalizedWarning], current: NormalizedWarning,
                notify_on_minor_update: bool = False) -> Optional[NormalizedWarning]:
    """返回需要处理的事件（含 event_type），None 表示本轮无需任何处理。"""
    prev_level = previous.warning_level if previous else None
    prev_rank = previous.rank() if previous else 0
    curr_rank = current.rank()

    if prev_rank == 0 and curr_rank == 0:
        return None

    event: Optional[NormalizedWarning] = None
    if prev_rank == 0 and curr_rank > 0:
        event = replace(current, event_type=EVENT_ISSUE, previous_level=prev_level)
    elif prev_rank > 0 and curr_rank == 0:
        event = replace(current, event_type=EVENT_CANCEL, previous_level=prev_level)
    elif curr_rank > prev_rank:
        event = replace(current, event_type=EVENT_UPGRADE, previous_level=prev_level)
    elif curr_rank < prev_rank:
        event = replace(current, event_type=EVENT_DOWNGRADE, previous_level=prev_level)
    else:
        level_changed = previous.warning_level != current.warning_level
        time_changed = (previous.update_time != current.update_time) or \
                        (previous.source_payload_hash != current.source_payload_hash)
        if not (level_changed or time_changed):
            return None
        if notify_on_minor_update or level_changed:
            event = replace(current, event_type=EVENT_UPDATE, previous_level=prev_level)
        else:
            logger.info("%s 更新时间变化但等级未变，且未启用重要更新通知，仅更新本地记录", current.warning_category)
            return replace(current, event_type=EVENT_UPDATE, previous_level=prev_level, delivery_status="SKIPPED_NO_NOTIFY")

    if event is not None:
        logger.info("检测到事件: category=%s level=%s event_type=%s previous_level=%s",
                    event.warning_category, event.warning_level, event.event_type, prev_level)
    return event


# ============================================================
# 4. 工作时间与发送策略层
# ============================================================
HKT = ZoneInfo("Asia/Hong_Kong")


def now_hkt() -> datetime:
    return datetime.now(HKT)


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def is_within_work_hours(settings, now: Optional[datetime] = None) -> bool:
    now = now or now_hkt()
    if not settings.work_hours_enabled:
        return True
    if now.weekday() not in settings.workdays:
        return False
    start, end = _parse_hhmm(settings.work_start_time), _parse_hhmm(settings.work_end_time)
    return start <= now.time() <= end


def is_high_priority(level: str) -> bool:
    return level in HIGH_PRIORITY_LEVELS


def should_dispatch_to_group(event: NormalizedWarning, group: GroupConfig, settings,
                               now: Optional[datetime] = None) -> tuple[bool, str]:
    if not group.enabled:
        return False, "群组未启用"
    if group.is_24x7:
        return True, "群组配置为 24/7 全量接收"
    if is_high_priority(event.warning_level):
        return True, "高等级告警（八号以上/黑雨），任何时间立即发送"
    if is_within_work_hours(settings, now):
        return True, "工作时间内，按常规规则发送"
    return False, "工作时间外，低等级告警按规则抑制"


def should_resend_on_work_start(settings) -> bool:
    return bool(settings.resend_on_work_start)


# ============================================================
# 5. 群组订阅匹配层
# ============================================================
def group_subscribes_event(event: NormalizedWarning, group: GroupConfig) -> bool:
    if event.warning_category == CAT_TROPICAL_CYCLONE:
        return group.subscribe_tc

    if event.warning_category == CAT_HOT_WEATHER:
        return group.subscribe_hot_weather

    if event.warning_category == CAT_HEAT_STRESS_WORK:
        return group.subscribe_heat_stress_work

    if event.warning_category == CAT_RAINSTORM:
        # CANCEL 时 current level 是 RAIN_NONE，
        # 因此必须按照取消前的等级决定群组订阅。
        subscription_level = (
            event.previous_level
            if event.event_type == EVENT_CANCEL and event.previous_level
            else event.warning_level
        )

        logger.info(
            "雨暴订阅判断：group=%s, event_type=%s, "
            "current_level=%s, previous_level=%s, subscription_level=%s",
            group.display_name,
            event.event_type,
            event.warning_level,
            event.previous_level,
            subscription_level,
        )

        if subscription_level == RAIN_YELLOW:
            return group.subscribe_rain_yellow

        if subscription_level == RAIN_RED:
            return group.subscribe_rain_red

        if subscription_level == RAIN_BLACK:
            return group.subscribe_rain_black

    return False

# ============================================================
# 6. 通知文本构建层
# ============================================================
EVENT_DISPLAY = {EVENT_ISSUE: "发布", EVENT_UPGRADE: "升级", EVENT_DOWNGRADE: "降级",
                  EVENT_CANCEL: "取消", EVENT_UPDATE: "更新",EVENT_RESEND_WORK_HOURS: "工作时间补发"}
CATEGORY_TITLE = {CAT_TROPICAL_CYCLONE: "香港天文台热带气旋警告", CAT_RAINSTORM: "香港天文台暴雨警告",
                   CAT_HOT_WEATHER: "香港天文台酷热天气警告"}


def _fmt_time(t: Optional[str]) -> str:
    if not t:
        return "未提供"
    try:
        date_part, time_part = t.split("T")
        return f"{date_part} {time_part[:5]}"
    except Exception:
        return t


def is_urgent(event: NormalizedWarning) -> bool:
    return event.warning_level in HIGH_PRIORITY_LEVELS


def build_single_message(event: NormalizedWarning, settings, group_prefix: str = "") -> str:
    urgent = is_urgent(event)
    header = "🚨 紧急运维天气警告" if urgent else settings.message_prefix
    title = CATEGORY_TITLE.get(event.warning_category, "香港天文台天气警告")
    status = EVENT_DISPLAY.get(event.event_type, event.event_type)

    lines: List[str] = [header, ""]
    if group_prefix:
        lines.append(f"【{group_prefix}】")
    lines.append(f"【{title}】")
    lines.append(f"状态：{status}")

    if event.event_type in (EVENT_UPGRADE, EVENT_DOWNGRADE) and event.previous_level:
        prev_name = level_display_name(event.warning_category, event.previous_level)
        lines.append(f"原信号：{prev_name}")
        lines.append(f"当前信号：{event.display_name()}")
    else:
        label = "告警" if event.warning_category == CAT_HOT_WEATHER else "当前信号"
        lines.append(f"{label}：{event.display_name()}")

    if settings.include_hko_time:
        time_label = "HKO 更新时间" if event.event_type == EVENT_UPDATE else "HKO 发布时间"
        time_value = event.update_time if event.event_type == EVENT_UPDATE else event.issue_time
        lines.append(f"{time_label}：{_fmt_time(time_value or event.issue_time)}")

    if settings.include_detected_time:
        lines.append(f"程序检测时间：{_fmt_time(event.detected_time)}")

    lines.append("")
    if urgent:
        lines.append("此为高等级告警，已按 24/7 紧急规则自动发送。")
        lines.append("请相关人员按应急程序处理。")
    else:
        lines.append(settings.message_suffix)
    return "\n".join(lines)


def build_merged_message(events: List[NormalizedWarning], settings, group_prefix: str = "") -> str:
    if len(events) == 1:
        return build_single_message(events[0], settings, group_prefix)

    urgent = any(is_urgent(e) for e in events)
    header = "🚨 紧急运维天气警告" if urgent else settings.message_prefix
    lines: List[str] = [header, ""]
    if group_prefix:
        lines.append(f"【{group_prefix}】")
    lines.append("本轮共检测到以下告警变化：")
    lines.append("")
    for e in events:
        status = EVENT_DISPLAY.get(e.event_type, e.event_type)
        title = CATEGORY_TITLE.get(e.warning_category, "天气警告")
        lines.append(f"● {title} - {status}：{e.display_name()}")
        if settings.include_hko_time:
            lines.append(f"  HKO 时间：{_fmt_time(e.update_time or e.issue_time)}")
    if settings.include_detected_time:
        lines.append("")
        lines.append(f"程序检测时间：{_fmt_time(events[0].detected_time)}")
    lines.append("")
    lines.append(settings.message_suffix)
    return "\n".join(lines)
