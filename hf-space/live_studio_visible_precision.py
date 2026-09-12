from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import mss
from PIL import Image, ImageOps, ImageStat

import live_studio_visible as visible


def _app_pids() -> list[int]:
    rows: list[int] = []
    try:
        proc_dirs = list(Path('/proc').iterdir())
    except Exception:
        return rows
    for proc in proc_dirs:
        if not proc.name.isdigit():
            continue
        try:
            raw = (proc / 'cmdline').read_bytes()
            args = [part.decode('utf-8', errors='ignore') for part in raw.split(b'\x00') if part]
        except Exception:
            continue
        if not args:
            continue
        first = args[0].replace('\\', '/').lower().rstrip('/')
        first_name = first.rsplit('/', 1)[-1]
        rest = ' '.join(args[1:]).lower()
        # Explorer/start.exe include the TikTok EXE path as an argument. Only
        # argv[0] being TikTok LIVE Studio.exe counts as the actual app process.
        if first_name != 'tiktok live studio.exe' or '--type=' in rest:
            continue
        rows.append(int(proc.name))
    return rows


def _window_candidates(bridge: Any) -> list[tuple[str, bool, int]]:
    xdotool = shutil.which('xdotool')
    if not xdotool:
        return []
    env = visible._env(bridge)
    ids: dict[str, bool] = {}
    for pid in _app_pids():
        try:
            out = subprocess.check_output([xdotool, 'search', '--onlyvisible', '--pid', str(pid)], env=env, text=True, timeout=4)
            for wid in out.splitlines():
                wid = wid.strip()
                if wid:
                    ids[wid] = True
        except Exception:
            pass
    # Keep the Wine desktop only as a diagnostic fallback. It never outranks a
    # real app-owned window and is never considered proof of LIVE Studio UI.
    for pattern in ('RipoTikTok - Wine Desktop', 'RipoTikTok', 'Wine Desktop'):
        try:
            out = subprocess.check_output([xdotool, 'search', '--onlyvisible', '--name', pattern], env=env, text=True, timeout=4)
            for wid in out.splitlines():
                wid = wid.strip()
                if wid and wid not in ids:
                    ids[wid] = False
        except Exception:
            pass
    result: list[tuple[str, bool, int]] = []
    for wid, app_owned in ids.items():
        try:
            _, _, w, h = visible._geometry(bridge, wid)
            area = int(w) * int(h)
        except Exception:
            area = 0
        result.append((wid, app_owned, area))
    result.sort(key=lambda row: (row[1], row[2]), reverse=True)
    return result


def _wine_window(bridge: Any) -> str:
    choices = _window_candidates(bridge)
    app_owned = [row for row in choices if row[1] and row[2] >= 80_000]
    if app_owned:
        return app_owned[0][0]
    raise RuntimeError('TikTok LIVE Studio process is running but has not mapped a usable app-owned window.')


def _capture(bridge: Any) -> tuple[Image.Image, tuple[int, int, int, int], dict[str, Any]]:
    window = _wine_window(bridge)
    visible._activate(bridge, window)
    x, y, w, h = visible._geometry(bridge, window)
    old = os.environ.get('DISPLAY')
    os.environ['DISPLAY'] = visible._env(bridge)['DISPLAY']
    try:
        with mss.mss() as grabber:
            shot = grabber.grab({'left': x, 'top': y, 'width': w, 'height': h})
            image = Image.frombytes('RGB', shot.size, shot.rgb)
    finally:
        if old is None:
            os.environ.pop('DISPLAY', None)
        else:
            os.environ['DISPLAY'] = old
    stat = ImageStat.Stat(image.convert('L'))
    signal = {
        'capture_width': w,
        'capture_height': h,
        'screen_mean': round(float(stat.mean[0]), 2),
        'screen_stddev': round(float(stat.stddev[0]), 2),
        'app_window_candidates': sum(1 for _, owned, area in _window_candidates(bridge) if owned and area >= 80_000),
    }
    return image, (x, y, w, h), signal


def _ocr_lines(bridge: Any) -> list[visible.Hit]:
    tesseract = shutil.which('tesseract')
    if not tesseract:
        raise RuntimeError('tesseract-ocr is not installed on the server yet.')
    image, (offset_x, offset_y, _, _), signal = _capture(bridge)
    scale = 2
    gray = ImageOps.autocontrast(image.convert('L'))
    enlarged = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
    with tempfile.NamedTemporaryFile(prefix='ripo-live-ui-', suffix='.png', delete=False) as handle:
        temp = Path(handle.name)
    try:
        enlarged.save(temp, format='PNG')
        proc = subprocess.run(
            [tesseract, str(temp), 'stdout', '--psm', '11', '-l', 'eng', 'tsv'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False, text=True,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RuntimeError('LIVE Studio screen OCR failed.')
        groups: dict[tuple[str, str, str, str], list[tuple[str, int, int, int, int, float]]] = {}
        reader = csv.DictReader(io.StringIO(proc.stdout), delimiter='\t')
        for row in reader:
            text = re.sub(r'\s+', ' ', str(row.get('text') or '')).strip()
            if not text:
                continue
            try:
                conf = float(row.get('conf') or -1)
                left = int(row.get('left') or 0) // scale
                top = int(row.get('top') or 0) // scale
                width = max(1, int(row.get('width') or 0) // scale)
                height = max(1, int(row.get('height') or 0) // scale)
            except ValueError:
                continue
            if conf < 12:
                continue
            key = (str(row.get('block_num') or ''), str(row.get('par_num') or ''), str(row.get('line_num') or ''), str(row.get('page_num') or ''))
            groups.setdefault(key, []).append((text, left, top, width, height, conf))

        hits: list[visible.Hit] = []
        for words in groups.values():
            words.sort(key=lambda item: item[1])
            count = len(words)
            for size in range(1, min(4, count) + 1):
                for start in range(0, count - size + 1):
                    subset = words[start:start+size]
                    left = min(w[1] for w in subset); top = min(w[2] for w in subset)
                    right = max(w[1]+w[3] for w in subset); bottom = max(w[2]+w[4] for w in subset)
                    hits.append(visible.Hit(
                        text=' '.join(w[0] for w in subset)[:120],
                        left=offset_x+left, top=offset_y+top,
                        width=right-left, height=bottom-top,
                        confidence=sum(w[5] for w in subset)/len(subset),
                    ))
            if count > 4:
                left = min(w[1] for w in words); top = min(w[2] for w in words)
                right = max(w[1]+w[3] for w in words); bottom = max(w[2]+w[4] for w in words)
                hits.append(visible.Hit(
                    text=' '.join(w[0] for w in words)[:180], left=offset_x+left, top=offset_y+top,
                    width=right-left, height=bottom-top, confidence=sum(w[5] for w in words)/len(words),
                ))
        visible._last_precision_signal = {**signal, 'ocr_hit_count': len(hits)}
        return hits
    finally:
        temp.unlink(missing_ok=True)


def _classify(hits: list[visible.Hit]) -> dict[str, list[visible.Hit]]:
    buckets: dict[str, list[visible.Hit]] = {k: [] for k in ('go_live','login','confirm','continue','guest','mic')}
    specs = {
        'go_live': (re.compile(r'\b(go\s*live|start\s*(live|stream(?:ing)?))\b', re.I), {'go live','start live','start stream','start streaming'}),
        'login': (re.compile(r'\b(log\s*in|sign\s*in)\b', re.I), {'log in','sign in'}),
        'confirm': (re.compile(r'\b(confirm|yes,?\s*go\s*live|go\s*live\s*now)\b', re.I), {'confirm','go live now','yes go live'}),
        'continue': (re.compile(r'\b(continue|authorize|allow|open\s*tiktok)\b', re.I), {'continue','authorize','allow','open tiktok'}),
        'guest': (re.compile(r'\b(guest|co-?host|multi-?guest)\b', re.I), {'guest','co-host','cohost','multi-guest'}),
        'mic': (re.compile(r'\b(mic|microphone|audio)\b', re.I), {'mic','microphone','audio'}),
    }
    for hit in hits:
        normalized = re.sub(r'[^a-z0-9]+', ' ', hit.text.lower()).strip()
        for key, (pattern, exact) in specs.items():
            if pattern.search(hit.text):
                setattr(hit, '_ripo_exact', normalized in exact)
                buckets[key].append(hit)
    for rows in buckets.values():
        rows.sort(key=lambda hit: (0 if getattr(hit, '_ripo_exact', False) else 1, len(hit.text), -hit.confidence))
    return buckets


def _visible_capabilities(bridge: Any) -> dict[str, Any]:
    try:
        hits = _ocr_lines(bridge)
        buckets = _classify(hits)
        signal = dict(getattr(visible, '_last_precision_signal', {}) or {})
        return {
            'ok': True,
            'visible_ui_ready': bool(hits) or signal.get('screen_stddev', 0) > 4,
            'ocr_ready': True,
            'go_live_available': bool(buckets['go_live']),
            'login_required': bool(buckets['login']) and not bool(buckets['go_live']),
            'confirm_available': bool(buckets['confirm']),
            'continue_available': bool(buckets['continue']),
            'guest_controls_visible': bool(buckets['guest']),
            'microphone_controls_visible': bool(buckets['mic']),
            'safe_action_labels': [
                *(['Go LIVE'] if buckets['go_live'] else []), *(['Login'] if buckets['login'] else []),
                *(['Confirm'] if buckets['confirm'] else []), *(['Continue'] if buckets['continue'] else []),
                *(['Guest'] if buckets['guest'] else []), *(['Microphone'] if buckets['mic'] else []),
            ],
            'ocr_hit_count': int(signal.get('ocr_hit_count') or 0),
            'app_window_candidates': int(signal.get('app_window_candidates') or 0),
            'screen_mean': signal.get('screen_mean'),
            'screen_stddev': signal.get('screen_stddev'),
        }
    except Exception as exc:
        choices = _window_candidates(bridge)
        return {
            'ok': True, 'visible_ui_ready': False, 'ocr_ready': bool(shutil.which('tesseract')),
            'go_live_available': False, 'login_required': False, 'confirm_available': False,
            'continue_available': False, 'guest_controls_visible': False, 'microphone_controls_visible': False,
            'safe_action_labels': [], 'visible_ui_error': str(exc)[:500], 'ocr_hit_count': 0,
            'app_window_candidates': sum(1 for _, owned, area in choices if owned and area >= 80_000),
        }


visible._app_pids = _app_pids
visible._window_candidates = _window_candidates
visible._wine_window = _wine_window
visible._capture = _capture
visible._ocr_lines = _ocr_lines
visible._classify = _classify
visible.visible_capabilities = _visible_capabilities
