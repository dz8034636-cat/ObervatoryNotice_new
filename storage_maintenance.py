"""Storage maintenance: generated images, app.log and the Chromium cache.

No Qt imports here, so the logic can be tested on its own.

Rules
- generated_images : delete PNG files (files younger than 10 minutes are kept,
                     they may be in the middle of being sent).
- app.log          : keep only the last 3 days = today + the 2 days before it.
- chrome_profile   : delete ONLY a whitelist of cache folders. Login data
                     (Local Storage, IndexedDB, Cookies, Session Storage, ...) is
                     never touched, so WhatsApp stays logged in.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

LOG_KEEP_DAYS = 3
IMAGE_MIN_AGE_SECONDS = 600

CACHE_DIR_NAMES = (
    'Cache', 'Code Cache', 'GPUCache', 'DawnCache', 'DawnGraphiteCache',
    'DawnWebGPUCache', 'GraphiteDawnCache', 'GrShaderCache', 'ShaderCache',
    'Media Cache',
)
CACHE_SUBDIRS = (('Service Worker', 'CacheStorage'), ('Service Worker', 'ScriptCache'))

_TS = re.compile(r'^(\d{4})-(\d{2})-(\d{2}) \d{2}:\d{2}:\d{2}')
_ROTATED = re.compile(r'^app\.log\.(\d{4})-(\d{2})-(\d{2})$')


@dataclass
class CleanResult:
    freed_bytes: int = 0
    removed_items: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class Stats:
    size: int = 0
    count: int = 0
    extra: str = ''


def fmt_size(num: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if num < 1024 or unit == 'GB':
            return f'{num:.0f} {unit}' if unit == 'B' else f'{num:.1f} {unit}'
        num /= 1024
    return f'{num:.1f} GB'


def dir_stats(path: Path) -> Stats:
    total = count = 0
    stack = [str(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        pass
        except OSError:
            pass
    return Stats(total, count)


# ---------------------------------------------------------------- images
def image_stats(images_dir: Path) -> Stats:
    return dir_stats(images_dir) if images_dir.exists() else Stats()


def clear_generated_images(images_dir: Path, min_age_seconds: int = IMAGE_MIN_AGE_SECONDS,
                           now: Optional[float] = None) -> CleanResult:
    result = CleanResult()
    now = time.time() if now is None else now
    if not images_dir.exists():
        return result
    for item in images_dir.iterdir():
        try:
            if not item.is_file() or item.is_symlink():
                continue
            if now - item.stat().st_mtime < min_age_seconds:
                continue
            size = item.stat().st_size
            item.unlink()
            result.freed_bytes += size
            result.removed_items += 1
        except OSError as exc:
            result.errors.append(f'{item.name}: {exc}')
    return result


# ------------------------------------------------------------------ logs
def log_cutoff(today: Optional[date] = None, keep_days: int = LOG_KEEP_DAYS) -> date:
    return (today or date.today()) - timedelta(days=keep_days - 1)


def log_stats(logs_dir: Path) -> Stats:
    st = dir_stats(logs_dir) if logs_dir.exists() else Stats()
    oldest: Optional[date] = None
    live = logs_dir / 'app.log'
    candidates = []
    for p in logs_dir.glob('app.log*') if logs_dir.exists() else []:
        m = _ROTATED.match(p.name)
        if m:
            candidates.append(date(int(m[1]), int(m[2]), int(m[3])))
    if live.exists():
        try:
            with open(live, 'r', encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    m = _TS.match(line)
                    if m:
                        candidates.append(date(int(m[1]), int(m[2]), int(m[3])))
                        break
        except OSError:
            pass
    if candidates:
        oldest = min(candidates)
    st.extra = f'earliest {oldest.isoformat()}' if oldest else ''
    return st


def _file_handlers(path: Path):
    target = os.path.normcase(os.path.abspath(path))
    seen = []
    loggers = [logging.getLogger('hko_alert'), logging.getLogger()]
    for lg in loggers:
        for h in lg.handlers:
            if isinstance(h, logging.FileHandler) and h not in seen \
                    and os.path.normcase(os.path.abspath(h.baseFilename)) == target:
                seen.append(h)
    return seen


def _trim_live_log(path: Path, cutoff: date) -> CleanResult:
    result = CleanResult()
    if not path.exists():
        return result
    handlers = _file_handlers(path)
    for h in handlers:
        h.acquire()
    try:
        for h in handlers:
            if h.stream:
                h.stream.flush()
                h.stream.close()
                h.stream = None
        before = path.stat().st_size
        keep, current_ok, dropped = [], True, 0
        with open(path, 'r', encoding='utf-8', errors='replace', newline='') as fh:
            for line in fh:
                m = _TS.match(line)
                if m:
                    current_ok = date(int(m[1]), int(m[2]), int(m[3])) >= cutoff
                if current_ok:
                    keep.append(line)
                else:
                    dropped += 1
        if dropped:
            tmp = path.with_name(path.name + '.tmp')
            with open(tmp, 'w', encoding='utf-8', newline='') as fh:
                fh.writelines(keep)
            os.replace(tmp, path)
            result.freed_bytes += max(0, before - path.stat().st_size)
            result.removed_items += dropped
    except OSError as exc:
        result.errors.append(f'{path.name}: {exc}')
    finally:
        for h in handlers:
            h.release()
    return result


def purge_logs(logs_dir: Path, keep_days: int = LOG_KEEP_DAYS,
               today: Optional[date] = None) -> CleanResult:
    cutoff = log_cutoff(today, keep_days)
    result = CleanResult()
    if not logs_dir.exists():
        return result
    for p in logs_dir.glob('app.log.*'):
        if p.name.endswith('.tmp'):
            continue
        m = _ROTATED.match(p.name)
        try:
            if m:
                file_day = date(int(m[1]), int(m[2]), int(m[3]))
            else:
                file_day = datetime.fromtimestamp(p.stat().st_mtime).date()
            if file_day < cutoff:
                size = p.stat().st_size
                p.unlink()
                result.freed_bytes += size
                result.removed_items += 1
        except OSError as exc:
            result.errors.append(f'{p.name}: {exc}')
    live = _trim_live_log(logs_dir / 'app.log', cutoff)
    result.freed_bytes += live.freed_bytes
    result.removed_items += live.removed_items
    result.errors += live.errors
    return result


# ---------------------------------------------------------- chrome cache
def _profile_dirs(profile_root: Path) -> List[Path]:
    """Chromium user-data dirs (session, session-<id>) and their profiles."""
    roots: List[Path] = []
    if not profile_root.exists():
        return roots
    if (profile_root / 'Default').is_dir():
        roots.append(profile_root)
    for child in profile_root.iterdir():
        if child.is_dir() and child.name.startswith('session'):
            roots.append(child)
    profiles: List[Path] = []
    for root in roots:
        profiles.append(root)
        if (root / 'Default').is_dir():
            profiles.append(root / 'Default')
        for sub in root.iterdir():
            if sub.is_dir() and sub.name.startswith('Profile '):
                profiles.append(sub)
    return profiles


def cache_targets(profile_root: Path) -> List[Path]:
    base = profile_root.resolve()
    found: List[Path] = []
    for prof in _profile_dirs(profile_root):
        names = [prof / n for n in CACHE_DIR_NAMES]
        names += [prof.joinpath(*parts) for parts in CACHE_SUBDIRS]
        for p in names:
            try:
                if p.is_dir() and not p.is_symlink():
                    rp = p.resolve()
                    if base in rp.parents and rp not in found:
                        found.append(rp)
            except OSError:
                pass
    return found


def chrome_stats(profile_root: Path) -> Stats:
    total = dir_stats(profile_root) if profile_root.exists() else Stats()
    cache_bytes = cache_files = 0
    targets = cache_targets(profile_root)
    for t in targets:
        s = dir_stats(t)
        cache_bytes += s.size
        cache_files += s.count
    return Stats(cache_bytes, cache_files, f'{len(targets)}|{total.size}')


def clear_chrome_cache(profile_root: Path) -> CleanResult:
    result = CleanResult()
    for target in cache_targets(profile_root):
        size = dir_stats(target).size
        errors: List[str] = []

        def _onerror(func, path, exc_info):
            errors.append(f'{path}: {exc_info[1]}')

        shutil.rmtree(target, onerror=_onerror)
        if errors:
            result.errors += errors[:3]
        if not target.exists():
            result.freed_bytes += size
            result.removed_items += 1
    return result
