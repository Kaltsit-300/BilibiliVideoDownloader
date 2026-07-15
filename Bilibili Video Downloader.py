"""
Bilibili Video Downloader - B站视频下载器
支持：高质量视频下载 / 封面图片下载 / 音频下载 / 画质选择（需 Cookie 解锁高画质）
      Cookie 自动获取（扫码登录 + 本地缓存） / 合集多选并行下载
"""

import requests
import json
import re
import os
import time
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import uuid
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, Toplevel
from urllib.parse import urlparse, parse_qs, unquote, quote_plus
import io
import contextlib
import webbrowser
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
try:
    from moviepy.video.io import ffmpeg_tools
except ImportError:
    ffmpeg_tools = None

REQUIRED_COOKIE_KEYS = {"SESSDATA", "bili_jct", "DedeUserID"}
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bili_cookie_cache")

# 画质表（从高到低）
QUALITY_NAME = {
    127: "8K 超清",
    120: "4K 超清",
    116: "1080P 高码率",
    112: "1080P 60帧",
    80: "1080P 高清",
    74: "720P 60帧",
    64: "720P 高清",
    32: "480P 清晰",
    16: "360P 流畅",
}
# 需要登录（Cookie）才能选择的画质
COOKIE_REQUIRED_QUALITIES = {127, 120, 116, 112}


# ============================================================
# Cookie 工具函数
# ============================================================

def _cookie_dict_to_header(cookie_dict):
    filtered = {k: v for k, v in cookie_dict.items() if v}
    return "; ".join(f"{k}={v}" for k, v in filtered.items())


def _is_cookie_usable(cookie_header):
    if not cookie_header:
        return False
    # SESSDATA 是 B 站鉴权的核心字段，必须存在才认为可用；
    # 同时再要求 bili_jct / DedeUserID 中至少一个，避免只拿到残缺 Cookie 被误判有效。
    if "SESSDATA=" not in cookie_header:
        return False
    return any(f"{k}=" in cookie_header for k in ("bili_jct", "DedeUserID"))


def _parse_cookie_from_callback_url(callback_url):
    parsed = urlparse(callback_url)
    query = parse_qs(parsed.query)
    key_order = [
        "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
        "sid", "buvid3", "buvid4"
    ]
    cookie_dict = {}
    for key in key_order:
        values = query.get(key, [])
        if values:
            cookie_dict[key] = unquote(values[0])
    return _cookie_dict_to_header(cookie_dict)


def _validate_bilibili_cookie(cookie_str):
    """验证 Cookie 是否有效。返回 (is_valid, msg, is_network_error)"""
    if not cookie_str or not _is_cookie_usable(cookie_str):
        return False, "Cookie 为空或缺少关键字段", False
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Cookie": cookie_str,
        }
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=headers, timeout=15
        )
        data = resp.json()
        if data.get("code") == 0:
            info = data.get("data", {})
            uname = info.get("uname", "")
            vip = info.get("vipStatus", 0)
            level = info.get("level_info", {}).get("current_level", "?")
            return True, f"已登录 [{uname}] | 等级 Lv{level} | {'大会员' if vip else '普通用户'}", False
        else:
            return False, f"Cookie 已失效 (code={data.get('code')})", False
    except requests.exceptions.RequestException as e:
        return False, f"网络验证失败: {e}", True


def _save_cookie_cache(cookie_str):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(cookie_str)
    except Exception as e:
        print(f"   - 保存缓存失败: {e}")


def _load_cookie_cache():
    """加载本地缓存的 Cookie。返回 (cookie_str, msg) 或 ("", "")"""
    if not os.path.exists(CACHE_FILE):
        return "", ""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cached = f.read().strip()
        if not cached or not _is_cookie_usable(cached):
            return "", "缓存文件为空或无效"
        # 验证是否仍然有效
        is_valid, msg, is_network_error = _validate_bilibili_cookie(cached)
        if is_valid:
            return cached, f"使用缓存: {msg}"
        if is_network_error:
            print(f"   - [缓存] 网络不通，保留缓存直接使用")
            return cached, "[缓存] 网络不通，使用离线缓存"
        # B站明确返回过期，清除缓存
        try:
            os.remove(CACHE_FILE)
        except Exception:
            pass
        return "", f"[缓存] 已过期: {msg}"
    except Exception as e:
        return "", f"读取缓存失败: {e}"


def _clear_cookie_cache():
    if os.path.exists(CACHE_FILE):
        try:
            os.remove(CACHE_FILE)
        except Exception:
            pass


def _get_cookie_by_qr_login(root_window=None):
    """通过 QR 扫码登录获取 Cookie。支持弹窗显示二维码。"""
    print("   - 尝试扫码登录获取 Cookie ...")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    qr_ui = {"window": None, "status": None, "closed": False}
    try:
        gen_api = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
        gen_data = requests.get(gen_api, headers=headers, timeout=15).json()
        if gen_data.get("code") != 0:
            print(f"   - 二维码生成失败: {gen_data}")
            return ""

        qrcode_key = gen_data["data"]["qrcode_key"]
        qrcode_url = gen_data["data"]["url"]
        desktop_qr_url = (
            "https://api.qrserver.com/v1/create-qr-code/"
            f"?size=320x320&data={quote_plus(qrcode_url)}"
        )

        # Tk 窗口必须在主线程创建和更新；后台线程只负责网络轮询。
        if root_window and Image:
            try:
                qr_img_data = requests.get(desktop_qr_url, timeout=15).content
                ready = threading.Event()

                def create_qr_window():
                    try:
                        pil_image = Image.open(io.BytesIO(qr_img_data))
                        tk_image = ImageTk.PhotoImage(pil_image)

                        qr_window = Toplevel(root_window)
                        qr_window.title("B站扫码登录")
                        qr_window.geometry("360x430")
                        qr_window.configure(bg="#ffffff")
                        qr_window.resizable(False, False)
                        qr_window.transient(root_window)

                        tk.Label(
                            qr_window, text="请用 B站 App 扫描二维码",
                            bg="#ffffff", fg="#172033",
                            font=("Microsoft YaHei UI", 13, "bold")
                        ).pack(pady=(22, 10))

                        qr_label = tk.Label(qr_window, image=tk_image, bg="#ffffff")
                        qr_label.image = tk_image
                        qr_label.pack(padx=20, pady=8)

                        tk.Label(
                            qr_window,
                            text="打开 B站 App -> 扫一扫 -> 确认登录",
                            bg="#ffffff", fg="#5B6472",
                            font=("Microsoft YaHei UI", 9)
                        ).pack(pady=(2, 12))

                        status_lbl = tk.Label(
                            qr_window, text="等待扫码...",
                            bg="#ffffff", fg="#2563EB",
                            font=("Microsoft YaHei UI", 10)
                        )
                        status_lbl.pack()

                        def on_close():
                            qr_ui["closed"] = True
                            qr_window.destroy()

                        qr_window.protocol("WM_DELETE_WINDOW", on_close)
                        qr_window.update_idletasks()
                        x = root_window.winfo_x() + (root_window.winfo_width() - 360) // 2
                        y = root_window.winfo_y() + (root_window.winfo_height() - 430) // 2
                        qr_window.geometry(f"+{max(x, 0)}+{max(y, 0)}")

                        qr_ui["window"] = qr_window
                        qr_ui["status"] = status_lbl
                    except Exception as ui_err:
                        print(f"   - 二维码弹窗创建失败: {ui_err}")
                    finally:
                        ready.set()

                root_window.after(0, create_qr_window)
                if not ready.wait(timeout=5):
                    print("   - 二维码弹窗创建超时，请使用浏览器链接扫码。")
                if not qr_ui["window"]:
                    print(f"   - 请在浏览器中打开: {desktop_qr_url}")
            except Exception as img_err:
                print(f"   - 二维码图片加载失败: {img_err}")
                print(f"   - 请在浏览器中打开: {desktop_qr_url}")
        else:
            print("\n请在浏览器打开下面链接，然后用手机 B站 App 扫码：")
            print(desktop_qr_url)

        def update_qr_status(text, color="#2563EB"):
            if not root_window or not qr_ui.get("window") or qr_ui.get("closed"):
                return

            def apply_status():
                if qr_ui.get("status") and qr_ui["status"].winfo_exists():
                    qr_ui["status"].configure(text=text, fg=color)

            root_window.after(0, apply_status)

        def close_qr_window(delay_ms=0):
            if not root_window or not qr_ui.get("window") or qr_ui.get("closed"):
                return

            def destroy_window():
                win = qr_ui.get("window")
                if win and win.winfo_exists():
                    qr_ui["closed"] = True
                    win.destroy()

            root_window.after(delay_ms, destroy_window)

        poll_api = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
        for i in range(90):
            time.sleep(2)
            poll_data = requests.get(
                poll_api,
                params={"qrcode_key": qrcode_key},
                headers=headers,
                timeout=15
            ).json()
            if poll_data.get("code") != 0:
                continue
            inner = poll_data.get("data", {})
            status_code = inner.get("code")

            if qr_ui.get("closed"):
                print("   - 二维码窗口已关闭。")
                return ""
            if status_code == 86090:
                update_qr_status("扫码成功，请在手机上确认...", "#059669")
            elif status_code == 86038:
                update_qr_status("二维码已失效", "#DC2626")

            if status_code == 0:
                callback_url = inner.get("url", "")
                cookie = _parse_cookie_from_callback_url(callback_url)
                if _is_cookie_usable(cookie):
                    print("   - ✅ 扫码登录成功，已获取可用 Cookie。")
                    update_qr_status("登录成功", "#059669")
                    close_qr_window(1200)
                    return cookie
                print("   - 扫码成功但回调中未拿到完整 Cookie 字段。")
                close_qr_window()
                return ""
            if status_code == 86038:
                print("   - 二维码已失效，请重试。")
                close_qr_window()
                return ""

        print("   - 等待扫码超时。")
        close_qr_window()
        return ""
    except Exception as e:
        print(f"   - 二维码登录失败: {e}")
        return ""


def auto_get_bilibili_cookie(root_window=None):
    """
    获取 B站 Cookie 的主入口。
    策略（两层）：
      1. 本地缓存（之前扫码成功后保存的）
      2. QR 扫码登录（弹出二维码窗口）
    """
    print("\n🍪 正在尝试获取 B站 Cookie ...")

    # 第一层：本地缓存
    cookie, cache_msg = _load_cookie_cache()
    if cookie and _is_cookie_usable(cookie):
        print(f"   - [缓存] ✅ {cache_msg}")
        return cookie
    elif cache_msg:
        print(f"   - [缓存] {cache_msg}")

    # 第二层：扫码登录
    cookie = _get_cookie_by_qr_login(root_window=root_window)
    if _is_cookie_usable(cookie):
        _save_cookie_cache(cookie)
        return cookie

    print("⚠️ 未能获取到可用 Cookie，将使用未登录模式下载（可能仅低画质）。")
    return ""


# ============================================================
# 视频解析核心逻辑
# ============================================================

class DownloadCancelled(Exception):
    pass


def _build_bilibili_headers(referer_url, cookie_str=""):
    return {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/122.0.0.0 Safari/537.36'
        ),
        'Referer': referer_url,
        'Cookie': cookie_str
    }


def _safe_filename(name):
    cleaned = re.sub(r'[\\/:*?"<>|]', '_', str(name or "").strip())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(" .")
    return cleaned or "未命名视频"


def _unique_output_path(folder, filename):
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(folder, filename)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base} ({counter}){ext}")
        counter += 1
    return candidate


def _format_duration(seconds):
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _extract_initial_state(html_data):
    # 优先匹配官方模板（INITIAL_STATE 后紧跟 ";（function"）
    state_match = re.search(r'window\.__INITIAL_STATE__=(.*?);\(function', html_data)
    if not state_match:
        # 容错：部分页面结构变化后，后面可能紧跟 </script> 或其它分隔符
        state_match = re.search(
            r'window\.__INITIAL_STATE__=(.*?)(?:;</script>|</script>|;)', html_data, re.S
        )
    if not state_match:
        return {}
    raw = state_match.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        # 末尾可能带有多余的分号/注释，做截断重试
        try:
            return json.loads(raw.rstrip(";").rstrip())
        except Exception:
            return {}


def _extract_bvid(url, html_data=""):
    bvid_match = re.search(r"/video/(BV[0-9A-Za-z]+)", url or "")
    if not bvid_match:
        bvid_match = re.search(r'"bvid":"(BV[0-9A-Za-z]+)"', html_data or "")
    return bvid_match.group(1) if bvid_match else ""


def _page_title_from_html(html_data):
    title_match = re.search(r'<title.*?>(.*?)</title>', html_data)
    if not title_match:
        return "未命名视频"
    return _safe_filename(title_match.group(1).replace('_哔哩哔哩_bilibili', ''))


def _make_video_option(index, title, bvid, cid, duration=None, section_title=""):
    return {
        "index": index,
        "title": _safe_filename(title),
        "bvid": bvid,
        "cid": int(cid) if str(cid).isdigit() else cid,
        "duration": _format_duration(duration),
        "section": str(section_title or "").strip(),
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
    }


def _append_unique_option(options, seen, option):
    key = (option.get("bvid"), option.get("cid"))
    if not option.get("bvid") or not option.get("cid") or key in seen:
        return
    seen.add(key)
    options.append(option)


def _episode_title(ep):
    arc = ep.get("arc") or {}
    page = ep.get("page") or {}
    return (
        ep.get("title") or ep.get("long_title") or page.get("part") or
        arc.get("title") or f"P{ep.get('page', '')}"
    )


def _episode_bvid(ep, fallback_bvid):
    arc = ep.get("arc") or {}
    return ep.get("bvid") or arc.get("bvid") or fallback_bvid


def _episode_cid(ep):
    page = ep.get("page") or {}
    return ep.get("cid") or page.get("cid")


def _episode_duration(ep):
    arc = ep.get("arc") or {}
    page = ep.get("page") or {}
    return ep.get("duration") or page.get("duration") or arc.get("duration")


def _extract_video_options(initial_state, fallback_bvid):
    options = []
    seen = set()
    video_data = initial_state.get("videoData") or {}

    ugc_seasons = [
        video_data.get("ugc_season"),
        initial_state.get("ugc_season"),
        initial_state.get("ugcSeason"),
    ]
    for season in [item for item in ugc_seasons if isinstance(item, dict)]:
        for section in season.get("sections", []) or []:
            section_title = section.get("title") or season.get("title") or "合集"
            for ep in section.get("episodes", []) or []:
                option = _make_video_option(
                    len(options) + 1,
                    _episode_title(ep),
                    _episode_bvid(ep, fallback_bvid),
                    _episode_cid(ep),
                    _episode_duration(ep),
                    section_title,
                )
                _append_unique_option(options, seen, option)

    pages = video_data.get("pages", []) or []
    base_title = video_data.get("title") or initial_state.get("title") or "视频"
    for page in pages:
        part = page.get("part") or base_title
        title = part if len(pages) > 1 else base_title
        option = _make_video_option(
            len(options) + 1,
            title,
            video_data.get("bvid") or fallback_bvid,
            page.get("cid"),
            page.get("duration"),
            "分P" if len(pages) > 1 else "",
        )
        _append_unique_option(options, seen, option)

    if not options:
        cid = video_data.get("cid")
        if not cid:
            cid_match = re.search(r'"cid":(\d+)', json.dumps(initial_state, ensure_ascii=False))
            cid = cid_match.group(1) if cid_match else None
        if cid:
            option = _make_video_option(
                1,
                video_data.get("title") or "当前视频",
                video_data.get("bvid") or fallback_bvid,
                cid,
                video_data.get("duration"),
                "",
            )
            _append_unique_option(options, seen, option)

    for i, option in enumerate(options, 1):
        option["index"] = i
    return options


def describe_video_option(option):
    section = f"[{option['section']}] " if option.get("section") else ""
    duration = f"  {option['duration']}" if option.get("duration") else ""
    return f"{option['index']:02d}. {section}{option['title']}{duration}"


def _fetch_options_via_api(bvid, cookie_str, emit):
    """降级方案：当页面 __INITIAL_STATE__ 解析失败时，改用 B 站官方 view 接口获取分P列表。
    官方接口比抓取 HTML 更稳定，能有效抵御页面改版导致的解析失败。"""
    try:
        headers = _build_bilibili_headers(f"https://www.bilibili.com/video/{bvid}", cookie_str)
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid}, headers=headers, timeout=20
        )
        data = resp.json()
        if data.get("code") != 0:
            emit(f"备用接口返回错误 (code={data.get('code')})")
            return None
        v = data.get("data", {})
        pages = v.get("pages") or []
        if not pages:
            return None
        options = []
        for idx, p in enumerate(pages, start=1):
            options.append(_make_video_option(
                idx, p.get("part") or v.get("title", ""), bvid, p.get("cid"),
                duration=p.get("duration"), section_title=""
            ))
        emit(f"通过备用接口解析完成：找到 {len(options)} 个可选视频。")
        return {
            "page_title": _safe_filename(v.get("title", "未命名视频")),
            "source_url": f"https://www.bilibili.com/video/{bvid}",
            "options": options,
            "cover_url": v.get("pic", ""),
        }
    except Exception as e:
        emit(f"备用解析接口也失败: {e}")
        return None


def fetch_bilibili_video_options(url, cookie_str="", logger=None):
    def emit(msg):
        if logger:
            logger(msg)
        else:
            print(msg)

    if not re.match(r"^https?://", url or ""):
        raise ValueError("链接格式不正确，请粘贴完整的 B站视频链接。")

    headers = _build_bilibili_headers(url, cookie_str)
    emit("正在解析链接中的视频列表...")
    response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    response.encoding = 'utf-8'
    html_data = response.text
    final_url = response.url or url
    bvid = _extract_bvid(final_url, html_data)
    if not bvid:
        raise ValueError("解析失败，无法识别 BVID。请确认链接是普通视频页或合集中的视频页。")

    initial_state = _extract_initial_state(html_data)
    page_title = _page_title_from_html(html_data)
    options = _extract_video_options(initial_state, bvid) if initial_state else []
    cover_url = ""
    if initial_state:
        video_data = initial_state.get("videoData") or {}
        cover_url = video_data.get("pic") or initial_state.get("picture") or ""

    if not options:
        emit("页面结构解析失败或为空，尝试官方备用接口...")
        fallback = _fetch_options_via_api(bvid, cookie_str, emit)
        if fallback:
            return fallback
        raise ValueError("解析失败，未找到可下载的视频条目（页面与备用接口均失败）。")

    emit(f"解析完成：找到 {len(options)} 个可选视频。")
    return {
        "page_title": page_title,
        "source_url": final_url,
        "options": options,
        "cover_url": cover_url,
    }


def fetch_bilibili_qualities(bvid, cid, cookie_str=""):
    """查询当前账号实际拿到的可下载画质 ID 集合。

    DASH 视频流以 dash.video 真实返回为准；未登录低画质有时只返回
    MP4/durl 渐进流，因此再按候选画质探测一次 durl 的实际 quality。
    不使用 accept_quality 作为可下载依据。
    """
    qualities = set()
    try:
        referer_url = f"https://www.bilibili.com/video/{bvid}" if bvid else "https://www.bilibili.com"
        headers = _build_bilibili_headers(referer_url, cookie_str)
        playurl_api = "https://api.bilibili.com/x/player/playurl"

        dash_params = {"bvid": bvid, "cid": cid, "qn": 127, "fnval": 4048, "fourk": 1}
        dash_resp = requests.get(playurl_api, params=dash_params, headers=headers, timeout=20)
        dash_json = dash_resp.json()
        if dash_json.get("code") == 0:
            videos = dash_json.get("data", {}).get("dash", {}).get("video", []) or []
            qualities.update(v.get("id") for v in videos if v.get("id") in QUALITY_NAME)

        # MP4/durl 只记录接口实际返回的 quality，避免把展示项误判为可下载。
        # 4 个候选画质并行探测，避免串行等待拖慢解析。
        def _probe_mp4(qid):
            try:
                params = {"bvid": bvid, "cid": cid, "qn": qid, "fnval": 1, "fourk": 0}
                resp = requests.get(playurl_api, params=params, headers=headers, timeout=20)
                j = resp.json()
                if j.get("code") != 0:
                    return None
                data = j.get("data", {}) or {}
                return data.get("quality") if data.get("durl") else None
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=4) as ex:
            for q in ex.map(_probe_mp4, (80, 64, 32, 16)):
                if q in QUALITY_NAME:
                    qualities.add(q)
    except Exception:
        pass
    return qualities


# ============================================================
# 下载核心逻辑
# ============================================================

def _control_is_cancelled(control):
    return bool(control and control.get("cancel_event") and control["cancel_event"].is_set())


def _wait_if_paused(control):
    if not control or not control.get("pause_event"):
        return
    while not control["pause_event"].is_set():
        if _control_is_cancelled(control):
            raise DownloadCancelled("任务已取消")
        time.sleep(0.2)


def _raise_if_cancelled(control):
    if _control_is_cancelled(control):
        raise DownloadCancelled("任务已取消")


def _codec_pref(codecid):
    # 偏好顺序：HEVC(12) > AV1(13) > AVC(7)，其余最低
    return {12: 3, 13: 2, 7: 1}.get(codecid, 0)


def _pick_video_stream(video_list, quality_id, logger):
    """按画质选择视频流；显式选择不可用时返回 None，不静默回退。"""
    by_codec = {12: [], 13: [], 7: []}
    for v in video_list:
        c = v.get("codecid")
        if c in by_codec:
            by_codec[c].append(v)

    if quality_id is not None:
        for c in (12, 13, 7):
            matches = [v for v in by_codec[c] if v.get("id") == quality_id]
            if matches:
                return sorted(matches, key=lambda x: x.get("bandwidth", 0), reverse=True)[0]
        any_match = [v for v in video_list if v.get("id") == quality_id]
        if any_match:
            return sorted(any_match, key=lambda x: x.get("bandwidth", 0), reverse=True)[0]
        if logger:
            logger(f"所选画质 {QUALITY_NAME.get(quality_id, quality_id)} 没有可下载流。")
        return None

    for c in (12, 13, 7):
        if by_codec[c]:
            return sorted(by_codec[c], key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)), reverse=True)[0]
    return sorted(video_list, key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)), reverse=True)[0]


def _download_to_file(url, path, headers, logger, control, label="下载", progress_cb=None):
    """流式下载到本地文件，支持暂停/取消与进度回调（progress_cb 接收 0-100）。"""
    try:
        with requests.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            downloaded = 0
            last = -1
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    _wait_if_paused(control)
                    _raise_if_cancelled(control)
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        inner = min(100, int(downloaded * 100 / total))
                        if inner != last:
                            last = inner
                            if progress_cb:
                                progress_cb(inner)
        return True
    except DownloadCancelled:
        raise
    except Exception as e:
        logger(f"{label}失败: {e}")
        return False


def download_bilibili_video(
    url, cookie_str="", output_dir=None, logger=None, selected_option=None,
    control=None, progress_callback=None,
    quality_id=None, want_video=True, want_audio=True, cover_url=None
):
    desktop_path = output_dir or os.path.join(os.path.expanduser("~"), "Desktop")

    def emit(msg):
        if logger:
            logger(msg)
        else:
            print(msg)

    def progress(stage, percent=None, detail=""):
        if progress_callback:
            progress_callback(stage=stage, percent=percent, detail=detail)

    if not os.path.exists(desktop_path):
        os.makedirs(desktop_path)

    # ---- 解析目标视频 ----
    try:
        _raise_if_cancelled(control)
        if selected_option:
            bvid = selected_option.get("bvid")
            cid = selected_option.get("cid")
            title = _safe_filename(selected_option.get("title"))
            referer_url = selected_option.get("url") or url
            emit(f"\n已选择视频：{describe_video_option(selected_option)}")
        else:
            parsed = fetch_bilibili_video_options(url, cookie_str=cookie_str, logger=emit)
            selected_option = parsed["options"][0]
            bvid = selected_option.get("bvid")
            cid = selected_option.get("cid")
            title = _safe_filename(selected_option.get("title") or parsed.get("page_title"))
            referer_url = selected_option.get("url") or parsed.get("source_url") or url
    except DownloadCancelled:
        emit("任务已取消。")
        progress("已取消", 0, "已取消")
        return False
    except Exception as e:
        emit(str(e))
        return False

    if not bvid or not cid:
        emit("解析失败：缺少 BVID 或 CID，无法请求播放流。")
        return False

    # ---- 请求播放流 ----
    headers = _build_bilibili_headers(referer_url, cookie_str)
    progress("解析播放流", 2, "请求播放接口")

    playurl_api = "https://api.bilibili.com/x/player/playurl"
    play_params = {"bvid": bvid, "cid": cid, "qn": 127, "fnval": 4048, "fourk": 1}
    try:
        _wait_if_paused(control)
        _raise_if_cancelled(control)
        play_resp = requests.get(playurl_api, params=play_params, headers=headers, timeout=20)
        play_json = play_resp.json()
    except DownloadCancelled:
        emit("任务已取消。")
        progress("已取消", 0, "已取消")
        return False
    except Exception as e:
        emit(f"请求播放接口失败: {e}")
        return False

    if play_json.get("code") != 0:
        emit(f"播放接口返回失败: code={play_json.get('code')}, message={play_json.get('message')}")
        return False

    try:
        play_data = play_json.get("data", {}) or {}
        dash_data = play_data.get("dash", {}) or {}
        video_list = dash_data.get("video", []) or []
        audio_list = dash_data.get("audio", []) or []
        durl_list = play_data.get("durl", []) or []
        durl_quality = play_data.get("quality")
    except Exception:
        emit("解析失败，数据结构可能发生变化。")
        return False

    results = []

    # ---- 封面图片 ----
    if cover_url:
        progress("下载封面", 1, "封面")
        emit("正在下载封面图片 ...")
        cover_path = _unique_output_path(desktop_path, f"{title}.jpg")
        try:
            ok = _download_to_file(
                cover_url, cover_path,
                _build_bilibili_headers(referer_url, cookie_str),
                emit, control, label="封面"
            )
            if ok and os.path.exists(cover_path):
                emit(f"封面已保存：{cover_path}")
                results.append("封面")
            else:
                emit("封面下载失败，已跳过。")
        except DownloadCancelled:
            emit("任务已取消。")
            progress("已取消", 0, "已取消")
            if os.path.exists(cover_path):
                os.remove(cover_path)
            return False

    # ---- 仅音频 ----
    if want_audio and not want_video:
        if not audio_list:
            emit("未拿到音频流，无法下载音频。")
            return False
        best_audio = sorted(audio_list, key=lambda x: x["id"], reverse=True)[0]
        audio_url = best_audio.get("baseUrl") or best_audio.get("base_url")
        audio_path = _unique_output_path(desktop_path, f"{title}.m4a")
        try:
            emit("正在下载音频 (m4a) ...")
            progress("下载音频", 5, "连接中")
            ok = _download_to_file(
                audio_url, audio_path, headers, emit, control, label="音频",
                progress_cb=lambda p: progress("下载音频", 5 + p * 0.9, f"{int(p)}%")
            )
            if ok and os.path.exists(audio_path):
                emit(f"音频已保存：{audio_path}")
                results.append("音频")
            else:
                emit("音频下载失败。")
                return False
        except DownloadCancelled:
            emit("任务已取消，正在清理临时文件。")
            progress("已取消", 0, "已取消")
            if os.path.exists(audio_path):
                os.remove(audio_path)
            return False

    # ---- 视频（DASH 音视频合成；未登录低画质可回退到 MP4/durl 单文件流）----
    if want_video:
        if video_list and audio_list:
            target = _pick_video_stream(video_list, quality_id, emit)
            if target is None:
                available_ids = sorted({v.get("id") for v in video_list if v.get("id") in QUALITY_NAME}, reverse=True)
                available_names = "、".join(QUALITY_NAME.get(q, str(q)) for q in available_ids) or "无"
                emit(f"当前账号或该视频不支持所选画质【{QUALITY_NAME.get(quality_id, quality_id)}】。实际可下载：{available_names}")
                return False
            video_url = target.get("baseUrl") or target.get("base_url")
            video_id = target.get("id")
            best_audio = sorted(audio_list, key=lambda x: x["id"], reverse=True)[0]
            audio_url = best_audio.get("baseUrl") or best_audio.get("base_url")
            if not video_url or not audio_url:
                emit("解析失败：未拿到可用的音视频下载地址。")
                return False

            quality_str = QUALITY_NAME.get(video_id, f"未知画质(ID:{video_id})")
            codec_name = {12: "HEVC (H.265)", 13: "AV1", 7: "AVC (H.264)"}.get(target.get("codecid"), "未知编码")
            emit(f"画质【{quality_str}】| 编码【{codec_name}】开始下载")
            if video_id <= 32:
                emit("提示：当前画质 ≤480P，可能 Cookie 权限不足或已过期。")

            task_id = uuid.uuid4().hex[:10]
            video_temp = os.path.join(desktop_path, f".bili_video_{task_id}.m4s")
            audio_temp = os.path.join(desktop_path, f".bili_audio_{task_id}.m4s")
            output_filename = _unique_output_path(desktop_path, f"{title}.mp4")

            try:
                ok_video = _download_to_file(
                    video_url, video_temp, headers, emit, control, label="视频",
                    progress_cb=lambda p: progress("下载视频", 5 + p * 0.45, f"{int(p)}%")
                )
                if not ok_video:
                    emit("视频流下载失败，已停止合成。")
                    for tmp in (video_temp, audio_temp):
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    return False
                ok_audio = _download_to_file(
                    audio_url, audio_temp, headers, emit, control, label="音频",
                    progress_cb=lambda p: progress("下载音频", 50 + p * 0.35, f"{int(p)}%")
                )
                if not ok_audio:
                    emit("音频流下载失败，已停止合成。")
                    for tmp in (video_temp, audio_temp):
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    return False
            except DownloadCancelled:
                emit("任务已取消，正在清理临时文件。")
                progress("已取消", 0, "已取消")
                for tmp in (video_temp, audio_temp):
                    if os.path.exists(tmp):
                        os.remove(tmp)
                return False
            except Exception as e:
                emit(f"下载失败: {e}")
                for tmp in (video_temp, audio_temp):
                    if os.path.exists(tmp):
                        os.remove(tmp)
                return False

            emit(f"\n正在合成，输出文件：\n{output_filename}")
            progress("合成中", 92, "合并音视频")
            try:
                _raise_if_cancelled(control)
                if ffmpeg_tools is None:
                    emit("未安装 moviepy，无法合成音视频。请先安装: pip install moviepy")
                    return False
                if os.path.exists(output_filename):
                    os.remove(output_filename)
                ffmpeg_tools.ffmpeg_merge_video_audio(video_temp, audio_temp, output_filename)
                _raise_if_cancelled(control)
                progress("完成", 100, "完成")
                emit("视频下载与合成完成。")
                emit("提示：若默认播放器无法播放画面，请尝试 PotPlayer 或 VLC。")
                results.append("视频")
            except DownloadCancelled:
                emit("任务已取消。")
                progress("已取消", 0, "已取消")
                if os.path.exists(output_filename):
                    os.remove(output_filename)
                return False
            except Exception as e:
                emit(f"合成失败: {e}")
                return False
            finally:
                for tmp in (video_temp, audio_temp):
                    if os.path.exists(tmp):
                        os.remove(tmp)
        else:
            mp4_qid = quality_id or 32
            try:
                mp4_params = {"bvid": bvid, "cid": cid, "qn": mp4_qid, "fnval": 1, "fourk": 0}
                mp4_resp = requests.get(playurl_api, params=mp4_params, headers=headers, timeout=20)
                mp4_json = mp4_resp.json()
                if mp4_json.get("code") == 0:
                    mp4_data = mp4_json.get("data", {}) or {}
                    durl_list = mp4_data.get("durl", []) or []
                    durl_quality = mp4_data.get("quality")
            except Exception as e:
                emit(f"请求 MP4 低画质流失败: {e}")

            if not durl_list:
                emit("未拿到 DASH 或 MP4 视频流，可能视频受限、链接无效或账号权限不足。")
                return False
            if quality_id is not None and durl_quality != quality_id:
                emit(
                    f"所选画质【{QUALITY_NAME.get(quality_id, quality_id)}】不可用，"
                    f"接口实际返回【{QUALITY_NAME.get(durl_quality, durl_quality)}】。"
                )
                return False

            video_id = durl_quality or mp4_qid
            video_url = durl_list[0].get("url") or ""
            output_filename = _unique_output_path(desktop_path, f"{title}.mp4")
            if not video_url:
                emit("解析失败：MP4 流没有可用下载地址。")
                return False
            emit(f"画质【{QUALITY_NAME.get(video_id, video_id)}】| MP4 单文件流开始下载")
            progress("下载视频", 5, "连接中")
            try:
                ok = _download_to_file(
                    video_url, output_filename, headers, emit, control, label="视频",
                    progress_cb=lambda p: progress("下载视频", 5 + p * 0.95, f"{int(p)}%")
                )
                if ok and os.path.exists(output_filename):
                    progress("完成", 100, "完成")
                    emit(f"视频已保存：{output_filename}")
                    results.append("视频")
                else:
                    emit("视频下载失败。")
                    return False
            except DownloadCancelled:
                emit("任务已取消，正在清理文件。")
                progress("已取消", 0, "已取消")
                if os.path.exists(output_filename):
                    os.remove(output_filename)
                return False

    if results:
        emit(f"✅ 已完成：{', '.join(results)}")
        return True
    emit("未选择任何下载内容。")
    return False


# ============================================================
# GUI 界面（CustomTkinter）
# ============================================================

def _check_ffmpeg():
    """检测 ffmpeg 是否可用（合成音视频必须）。返回 (ok, msg)。"""
    # moviepy 通常自带 ffmpeg（imageio_ffmpeg）
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return True, f"ffmpeg 可用: {exe}"
    except Exception:
        pass
    # 回退到系统 PATH
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return True, f"ffmpeg 可用: {exe}"
    return False, (
        "未检测到 ffmpeg，音视频合成将失败。请安装 ffmpeg 并加入 PATH，"
        "或重装 moviepy（pip install moviepy）以使用其内置 ffmpeg。"
    )


def launch_gui():
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.configure(fg_color="#F4F6F8")
    root.title("Bilibili Video Downloader")
    root.geometry("1280x880")
    root.minsize(1080, 720)

    # ============================================================
    # 浅色工作台主题
    # ============================================================
    APP_BG = "#F4F6F8"
    PANEL = "#FFFFFF"
    PANEL_SOFT = "#EEF2F6"
    PANEL_HOVER = "#E6EDF5"
    BORDER = "#D8DEE6"
    BORDER_STRONG = "#B7C2D0"
    PRIMARY = "#2563EB"
    PRIMARY_HOVER = "#1D4ED8"
    PRIMARY_SOFT = "#DBEAFE"
    SUCCESS = "#059669"
    SUCCESS_SOFT = "#D1FAE5"
    WARNING = "#D97706"
    DANGER = "#DC2626"
    DANGER_HOVER = "#B91C1C"
    DANGER_SOFT = "#FEE2E2"
    TEXT_PRIMARY = "#172033"
    TEXT_SECONDARY = "#5B6472"
    TEXT_DIM = "#8A94A6"
    TEXT_DISABLED = "#A6AFBD"
    INPUT_BG = "#FFFFFF"
    CODE_BG = "#F8FAFC"
    MAX_CONCURRENT_DOWNLOADS = 3

    state = {
        "cookie": "",
        "video_options": [],
        "parsed_url": "",
        "tasks": {},
        "task_order": [],
        "task_queue": [],
        "active_downloads": 0,
        "quality_id": 80,
        "available_qualities": set(),
        "quality_checked": False,
        "parse_bvid": "",
        "parse_cid": "",
        "cover_url": "",
        "quality_widgets": [],
        "quality_info": {},
    }
    default_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    url_pattern = re.compile(r"https?://[^\s]+")

    cookie_status_var = ctk.StringVar(value="未登录")
    url_var = ctk.StringVar(value="")
    folder_var = ctk.StringVar(value=default_dir)
    task_count_var = ctk.StringVar(value="准备就绪")
    download_video_var = ctk.BooleanVar(value=True)
    download_audio_var = ctk.BooleanVar(value=False)
    download_cover_var = ctk.BooleanVar(value=False)

    def font(size, weight=None, family="Microsoft YaHei UI"):
        return ctk.CTkFont(family=family, size=size, weight=weight)

    def make_card(parent, **grid_options):
        frame = ctk.CTkFrame(
            parent, fg_color=PANEL, corner_radius=8,
            border_width=1, border_color=BORDER
        )
        if grid_options:
            frame.grid(**grid_options)
        return frame

    def make_button(parent, text, kind="primary", width=None, height=34, command=None):
        if kind == "primary":
            colors = dict(fg_color=PRIMARY, hover_color=PRIMARY_HOVER, text_color="#FFFFFF")
        elif kind == "danger":
            colors = dict(fg_color=DANGER, hover_color=DANGER_HOVER, text_color="#FFFFFF")
        elif kind == "ghost":
            colors = dict(fg_color="transparent", hover_color=PANEL_HOVER, text_color=TEXT_SECONDARY)
        else:
            colors = dict(fg_color=PANEL_SOFT, hover_color=PANEL_HOVER, text_color=TEXT_PRIMARY)
        button_kwargs = dict(
            master=parent, text=text, height=height, command=command,
            font=font(12, "bold" if kind == "primary" else None),
            corner_radius=7, border_width=0, **colors
        )
        if width is not None:
            button_kwargs["width"] = width
        return ctk.CTkButton(**button_kwargs)

    def make_label(parent, text="", size=12, weight=None, color=None, **kwargs):
        return ctk.CTkLabel(
            parent, text=text, font=font(size, weight),
            text_color=color or TEXT_PRIMARY, **kwargs
        )

    def set_cookie_status(text, color, bg=None):
        cookie_status_var.set(text)
        cookie_dot.configure(text_color=color)
        cookie_label.configure(text_color=color)
        cookie_badge.configure(fg_color=bg or PANEL_SOFT, border_color=color)

    main_frame = ctk.CTkFrame(root, fg_color="transparent")
    main_frame.pack(fill="both", expand=True, padx=22, pady=(18, 12))

    header = ctk.CTkFrame(main_frame, fg_color="transparent")
    header.pack(fill="x", pady=(0, 12))
    header.grid_columnconfigure(0, weight=1)

    title_block = ctk.CTkFrame(header, fg_color="transparent")
    title_block.grid(row=0, column=0, sticky="w")
    make_label(title_block, "Bilibili Video Downloader", 24, "bold").pack(anchor="w")
    make_label(title_block, "视频、音频、封面下载工作台", 11, color=TEXT_SECONDARY).pack(anchor="w", pady=(2, 0))

    cookie_badge = ctk.CTkFrame(
        header, fg_color=PANEL_SOFT, corner_radius=8,
        border_width=1, border_color=BORDER
    )
    cookie_badge.grid(row=0, column=1, sticky="e", padx=(12, 0))
    cookie_dot = make_label(cookie_badge, "●", 13, color=TEXT_DIM)
    cookie_dot.pack(side="left", padx=(12, 5), pady=8)
    cookie_label = make_label(cookie_badge, "", 12, "bold", TEXT_SECONDARY, textvariable=cookie_status_var)
    cookie_label.pack(side="left", padx=(0, 12), pady=8)
    make_label(header, "v2.1", 11, color=TEXT_DIM).grid(row=0, column=2, sticky="e", padx=(12, 0))

    control_card = make_card(main_frame)
    control_card.pack(fill="x", pady=(0, 12))
    control_card.grid_columnconfigure(1, weight=1)
    make_label(control_card, "下载来源", 15, "bold").grid(row=0, column=0, columnspan=4, sticky="w", padx=18, pady=(14, 10))

    make_label(control_card, "链接", 11, "bold", TEXT_SECONDARY, width=42, anchor="w").grid(row=1, column=0, sticky="w", padx=(18, 10), pady=5)
    url_entry = ctk.CTkEntry(
        control_card, textvariable=url_var, placeholder_text="粘贴 B站视频链接",
        height=36, border_width=1, corner_radius=7, fg_color=INPUT_BG,
        border_color=BORDER_STRONG, text_color=TEXT_PRIMARY,
        placeholder_text_color=TEXT_DIM, font=font(12)
    )
    url_entry.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(0, 18), pady=5)

    make_label(control_card, "目录", 11, "bold", TEXT_SECONDARY, width=42, anchor="w").grid(row=2, column=0, sticky="w", padx=(18, 10), pady=5)
    folder_entry = ctk.CTkEntry(
        control_card, textvariable=folder_var, height=36, border_width=1,
        corner_radius=7, fg_color=INPUT_BG, border_color=BORDER_STRONG,
        text_color=TEXT_PRIMARY, font=font(12)
    )
    folder_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=5)

    def choose_folder():
        selected = filedialog.askdirectory(initialdir=folder_var.get() or default_dir)
        if selected:
            folder_var.set(selected)

    folder_btn = make_button(control_card, "浏览", "secondary", width=72, command=choose_folder)
    folder_btn.grid(row=2, column=3, sticky="e", padx=(0, 18), pady=5)

    action_row = ctk.CTkFrame(control_card, fg_color="transparent")
    action_row.grid(row=3, column=0, columnspan=4, sticky="ew", padx=18, pady=(10, 6))
    action_row.grid_columnconfigure(1, weight=1)

    left_actions = ctk.CTkFrame(action_row, fg_color="transparent")
    left_actions.grid(row=0, column=0, sticky="w")
    parse_btn = make_button(left_actions, "解析列表", "primary", width=118)
    parse_btn.pack(side="left", padx=(0, 8))
    cookie_btn = make_button(left_actions, "获取 Cookie", "secondary", width=112)
    cookie_btn.pack(side="left", padx=(0, 8))
    clear_cache_btn = make_button(left_actions, "清除缓存", "ghost", width=88)
    clear_cache_btn.pack(side="left")

    check_group = ctk.CTkFrame(action_row, fg_color="transparent")
    check_group.grid(row=0, column=2, sticky="e")

    def make_check(parent, text, var):
        return ctk.CTkCheckBox(
            parent, text=text, variable=var, font=font(12),
            checkbox_width=19, checkbox_height=19, border_width=2,
            corner_radius=4, fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
            border_color=BORDER_STRONG, text_color=TEXT_PRIMARY
        )

    make_check(check_group, "视频 (mp4)", download_video_var).pack(side="left", padx=(0, 14))
    make_check(check_group, "音频 (m4a)", download_audio_var).pack(side="left", padx=(0, 14))
    make_check(check_group, "封面 (jpg)", download_cover_var).pack(side="left")

    quality_section = ctk.CTkFrame(control_card, fg_color="transparent")
    quality_section.grid(row=4, column=0, columnspan=4, sticky="ew", padx=18, pady=(4, 16))
    make_label(quality_section, "画质", 11, "bold", TEXT_SECONDARY, width=42, anchor="w").pack(side="left", padx=(0, 10))
    quality_grid = ctk.CTkFrame(quality_section, fg_color="transparent")
    quality_grid.pack(side="left", fill="x", expand=True)
    quality_hint = make_label(quality_section, "高画质需要登录 Cookie", 10, color=TEXT_DIM)
    quality_hint.pack(side="right", padx=(8, 0))

    content = ctk.CTkFrame(main_frame, fg_color="transparent")
    content.pack(fill="both", expand=True)
    content.grid_columnconfigure(0, weight=3, uniform="main")
    content.grid_columnconfigure(1, weight=2, uniform="main")
    content.grid_rowconfigure(0, weight=1)

    list_card = make_card(content, row=0, column=0, sticky="nsew", padx=(0, 10))
    list_card.grid_columnconfigure(0, weight=1)
    list_card.grid_rowconfigure(1, weight=1)
    list_head = ctk.CTkFrame(list_card, fg_color="transparent")
    list_head.grid(row=0, column=0, sticky="ew", padx=18, pady=(15, 10))
    make_label(list_head, "视频列表", 15, "bold").pack(side="left")
    list_count_label = make_label(list_head, "", 11, color=TEXT_DIM)
    list_count_label.pack(side="right")

    list_wrap = ctk.CTkScrollableFrame(
        list_card, corner_radius=8, border_width=1, border_color=BORDER,
        fg_color=CODE_BG, scrollbar_button_color=BORDER_STRONG,
        scrollbar_button_hover_color=TEXT_DIM
    )
    list_wrap.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
    list_wrap.grid_columnconfigure(0, weight=1)
    list_btn_row = ctk.CTkFrame(list_card, fg_color="transparent")
    list_btn_row.grid(row=2, column=0, sticky="ew", padx=18, pady=(2, 16))
    download_btn = make_button(list_btn_row, "下载选中视频", "primary", height=40)
    download_btn.pack(fill="x")

    right_col = ctk.CTkFrame(content, fg_color="transparent")
    right_col.grid(row=0, column=1, sticky="nsew")
    right_col.grid_rowconfigure(0, weight=1)
    right_col.grid_rowconfigure(1, weight=1)
    right_col.grid_columnconfigure(0, weight=1)

    task_card = make_card(right_col, row=0, column=0, sticky="nsew", pady=(0, 10))
    task_card.grid_columnconfigure(0, weight=1)
    task_card.grid_rowconfigure(1, weight=1)
    task_head = ctk.CTkFrame(task_card, fg_color="transparent")
    task_head.grid(row=0, column=0, sticky="ew", padx=18, pady=(15, 10))
    make_label(task_head, "下载任务", 15, "bold").pack(side="left")
    task_status_label = make_label(task_head, "", 11, color=TEXT_DIM, textvariable=task_count_var)
    task_status_label.pack(side="right")

    task_scroll = ctk.CTkScrollableFrame(
        task_card, corner_radius=8, border_width=1, border_color=BORDER,
        fg_color=CODE_BG, scrollbar_button_color=BORDER_STRONG,
        scrollbar_button_hover_color=TEXT_DIM
    )
    task_scroll.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
    task_scroll.grid_columnconfigure(0, weight=1)
    task_placeholder = make_label(task_scroll, "选择视频后点击“下载选中视频”\n任务将显示在这里", 11, color=TEXT_DIM, justify="center")
    task_placeholder.pack(expand=True, fill="both", pady=42)

    task_btn_row = ctk.CTkFrame(task_card, fg_color="transparent")
    task_btn_row.grid(row=2, column=0, sticky="ew", padx=18, pady=(2, 16))
    task_download_btn = make_button(task_btn_row, "开始下载", "primary", width=96)
    task_download_btn.pack(side="left", padx=(0, 6))
    pause_btn = make_button(task_btn_row, "暂停", "secondary", width=64)
    pause_btn.pack(side="left", padx=(0, 5))
    resume_btn = make_button(task_btn_row, "继续", "secondary", width=64)
    resume_btn.pack(side="left", padx=(0, 5))
    cancel_btn = make_button(task_btn_row, "取消", "danger", width=64)
    cancel_btn.pack(side="left", padx=(0, 6))
    clear_done_btn = make_button(task_btn_row, "清除已完成", "ghost", width=92)
    clear_done_btn.pack(side="right")

    log_card = make_card(right_col, row=1, column=0, sticky="nsew")
    log_card.grid_columnconfigure(0, weight=1)
    log_card.grid_rowconfigure(1, weight=1)
    log_head = ctk.CTkFrame(log_card, fg_color="transparent")
    log_head.grid(row=0, column=0, sticky="ew", padx=18, pady=(15, 10))
    make_label(log_head, "运行日志", 15, "bold").pack(side="left")
    clear_log_btn = make_button(log_head, "清空", "ghost", width=58, height=28)
    clear_log_btn.pack(side="right")

    log_textbox = ctk.CTkTextbox(
        log_card, font=ctk.CTkFont(family="Consolas", size=11),
        corner_radius=8, border_width=1, border_color=BORDER,
        fg_color=CODE_BG, text_color=TEXT_PRIMARY,
        activate_scrollbars=True, wrap="word",
        scrollbar_button_color=BORDER_STRONG,
        scrollbar_button_hover_color=TEXT_DIM
    )
    log_textbox.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 16))
    log_textbox.tag_config("success", foreground=SUCCESS)
    log_textbox.tag_config("warning", foreground=WARNING)
    log_textbox.tag_config("error", foreground=DANGER)
    log_textbox.tag_config("hyperlink", foreground=PRIMARY, underline=True)

    status_bar = ctk.CTkFrame(main_frame, fg_color="transparent")
    status_bar.pack(fill="x", pady=(8, 0))
    make_label(status_bar, "并发上限 3 个任务", 10, color=TEXT_DIM).pack(side="left")
    make_label(status_bar, "Bilibili Video Downloader", 10, color=TEXT_DIM).pack(side="right")

    def append_log(msg, tag=None):
        text = str(msg)
        log_textbox.configure(state="normal")
        pos = 0
        for match in url_pattern.finditer(text):
            if match.start() > pos:
                log_textbox.insert("end", text[pos:match.start()], tag or ())
            log_textbox.insert("end", match.group(0), "hyperlink")
            pos = match.end()
        if pos < len(text):
            log_textbox.insert("end", text[pos:], tag or ())
        log_textbox.insert("end", "\n")
        log_textbox.see("end")
        log_textbox.configure(state="disabled")

    def clear_log():
        log_textbox.configure(state="normal")
        log_textbox.delete("1.0", "end")
        log_textbox.configure(state="disabled")

    clear_log_btn.configure(command=clear_log)

    video_checkboxes = []
    video_option_map = []

    def refresh_video_list(options):
        for w in video_checkboxes:
            w.destroy()
        video_checkboxes.clear()
        video_option_map.clear()

        if not options:
            placeholder = make_label(list_wrap, "点击“解析列表”加载视频", 11, color=TEXT_DIM, justify="center")
            placeholder.pack(expand=True, fill="both", pady=36)
            video_checkboxes.append(placeholder)
            list_count_label.configure(text="")
            return

        list_count_label.configure(text=f"共 {len(options)} 个视频")
        for opt in options:
            section_tag = opt.get("section") or ""
            duration_tag = opt.get("duration") or ""
            row = ctk.CTkFrame(list_wrap, fg_color=PANEL, corner_radius=7, border_width=1, border_color=BORDER)
            row.pack(fill="x", padx=4, pady=4)
            row.grid_columnconfigure(2, weight=1)
            var = ctk.BooleanVar(value=False)

            def update_row(v=var, r=row):
                selected = bool(v.get())
                r.configure(fg_color=PRIMARY_SOFT if selected else PANEL, border_color=PRIMARY if selected else BORDER)

            cb = ctk.CTkCheckBox(
                row, text="", variable=var, command=update_row,
                width=20, height=20, checkbox_width=19, checkbox_height=19,
                border_width=2, corner_radius=4, fg_color=PRIMARY,
                hover_color=PRIMARY_HOVER, border_color=BORDER_STRONG
            )
            cb.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=10, sticky="w")
            make_label(row, f"{opt['index']:02d}", 11, "bold", PRIMARY, width=34).grid(row=0, column=1, rowspan=2, padx=(0, 8), pady=8)
            title_lbl = make_label(row, opt.get("title", "未命名视频"), 12, "bold", TEXT_PRIMARY, anchor="w", justify="left")
            title_lbl.grid(row=0, column=2, sticky="ew", padx=(0, 10), pady=(8, 1))
            meta = "  ·  ".join(x for x in (section_tag, duration_tag) if x)
            meta_lbl = make_label(row, meta or "普通视频", 10, color=TEXT_DIM, anchor="w")
            meta_lbl.grid(row=1, column=2, sticky="ew", padx=(0, 10), pady=(0, 8))

            def toggle_row(event=None, v=var, update=update_row):
                v.set(not v.get())
                update()

            row.bind("<Button-1>", toggle_row)
            title_lbl.bind("<Button-1>", toggle_row)
            meta_lbl.bind("<Button-1>", toggle_row)
            video_checkboxes.append(row)
            video_option_map.append({"var": var, "option": opt})

    refresh_video_list([])

    def select_quality(qid):
        info = state["quality_info"].get(qid)
        if not info or not info["enabled"]:
            return
        state["quality_id"] = qid
        for q, d in state["quality_info"].items():
            is_sel = (q == qid)
            d["btn"].configure(
                fg_color=PRIMARY if is_sel else PANEL,
                hover_color=PRIMARY_HOVER if is_sel else PANEL_HOVER,
                text_color="#FFFFFF" if is_sel else TEXT_PRIMARY,
                border_color=PRIMARY if is_sel else BORDER,
                border_width=1,
            )

    def rebuild_quality_buttons():
        for w in list(state["quality_widgets"]):
            w.destroy()
        state["quality_widgets"].clear()
        state["quality_info"].clear()
        cookie_present = bool(state["cookie"])
        available = state["available_qualities"]
        quality_checked = bool(state.get("quality_checked"))
        enabled_ids = []
        for qid, qlabel in QUALITY_NAME.items():
            enabled = quality_checked and (qid in available)
            if enabled:
                enabled_ids.append(qid)
            btn = ctk.CTkButton(
                quality_grid, text=qlabel, width=104, height=30,
                font=font(11, "bold"), corner_radius=7, border_width=1,
                fg_color=PANEL if enabled else PANEL_SOFT,
                hover_color=PANEL_HOVER if enabled else PANEL_SOFT,
                text_color=TEXT_PRIMARY if enabled else TEXT_DISABLED,
                border_color=BORDER if enabled else "#E2E8F0",
                state="normal" if enabled else "disabled",
                command=lambda qid=qid: select_quality(qid)
            )
            btn.pack(side="left", padx=(0, 6), pady=3)
            state["quality_widgets"].append(btn)
            state["quality_info"][qid] = {"btn": btn, "enabled": enabled}
        if enabled_ids:
            if state["quality_id"] not in enabled_ids:
                state["quality_id"] = max(enabled_ids)
            select_quality(state["quality_id"])
        if not quality_checked:
            quality_hint.configure(text="解析后按实际可下载流启用画质")
        elif enabled_ids:
            quality_hint.configure(text="已按接口实际返回的 DASH 流启用画质")
        else:
            quality_hint.configure(text="当前账号或视频没有返回可下载视频流")

    rebuild_quality_buttons()

    def update_task_count():
        total = len(state["task_order"])
        active = sum(1 for tid in state["task_order"] if state["tasks"][tid]["status"] not in ("已完成", "已取消", "失败"))
        done = total - active
        if total == 0:
            task_count_var.set("准备就绪")
        else:
            task_count_var.set(f"已完成 {done}/{total}  ·  活动 {active}  ·  运行 {state['active_downloads']}/{MAX_CONCURRENT_DOWNLOADS}")

    task_widgets = {}

    def refresh_task_row(task_id):
        info = state["tasks"].get(task_id)
        if not info:
            return
        if task_placeholder.winfo_exists():
            task_placeholder.pack_forget()
        w = task_widgets.get(task_id)
        if not w:
            frame = ctk.CTkFrame(task_scroll, fg_color=PANEL, corner_radius=7, border_width=1, border_color=BORDER)
            frame.pack(fill="x", padx=4, pady=4)
            frame.grid_columnconfigure(0, weight=1)
            title_lbl = make_label(frame, info["title"], 11, "bold", TEXT_PRIMARY, anchor="w")
            title_lbl.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 2))
            prog = ctk.CTkProgressBar(frame, height=8, corner_radius=4, border_width=0, progress_color=PRIMARY, fg_color=PANEL_SOFT)
            prog.grid(row=1, column=0, sticky="ew", padx=(10, 8), pady=(4, 8))
            prog.set(0.0)
            status_lbl = make_label(frame, "等待中", 10, color=TEXT_DIM, width=128, anchor="e")
            status_lbl.grid(row=1, column=1, sticky="e", padx=(0, 10), pady=(4, 8))
            w = {"frame": frame, "title": title_lbl, "progress": prog, "status_label": status_lbl}
            task_widgets[task_id] = w
        percent = float(info.get("percent") or 0)
        w["title"].configure(text=info["title"])
        w["progress"].set(min(1.0, max(0.0, percent / 100.0)))
        color = {"已完成": SUCCESS, "失败": DANGER, "已取消": TEXT_DIM, "下载中": PRIMARY, "已暂停": WARNING, "等待中": TEXT_DIM}.get(info["status"], TEXT_DIM)
        w["status_label"].configure(text=f"{info['status']} {int(percent)}%" if percent else info["status"], text_color=color)
        w["frame"].configure(border_color=color if info["status"] in ("已完成", "失败", "已暂停") else BORDER)
        update_task_count()

    def maybe_start_next_download():
        while state["active_downloads"] < MAX_CONCURRENT_DOWNLOADS and state["task_queue"]:
            task_id = state["task_queue"].pop(0)
            info = state["tasks"].get(task_id)
            if not info or info["status"] == "已取消":
                continue
            state["active_downloads"] += 1
            info["status"] = "下载中"
            info["stage"] = "准备下载"
            refresh_task_row(task_id)
            threading.Thread(target=download_worker, args=(task_id,), daemon=True).start()
        update_task_count()

    def do_parse():
        url = url_var.get().strip()
        if not url:
            append_log("请先输入视频链接", "warning")
            return
        append_log(f"正在解析: {url}")
        parse_btn.configure(state="disabled", text="解析中...")

        def parse_thread():
            try:
                result = fetch_bilibili_video_options(url, cookie_str=state["cookie"], logger=lambda m: root.after(0, lambda m=m: append_log("  " + m)))
                available = set()
                parse_bvid = ""
                parse_cid = ""
                if result["options"]:
                    opt = result["options"][0]
                    parse_bvid = opt.get("bvid", "")
                    parse_cid = opt.get("cid", "")
                    available = fetch_bilibili_qualities(parse_bvid, parse_cid, state["cookie"])

                def apply_result():
                    state["video_options"] = result["options"]
                    state["parsed_url"] = result["source_url"]
                    state["cover_url"] = result["cover_url"]
                    state["parse_bvid"] = parse_bvid
                    state["parse_cid"] = parse_cid
                    state["available_qualities"] = available
                    state["quality_checked"] = True
                    refresh_video_list(result["options"])
                    rebuild_quality_buttons()
                    append_log(f"解析完成：找到 {len(result['options'])} 个视频", "success")
                    if result["cover_url"]:
                        append_log("已获取封面地址，可勾选下载封面")
                root.after(0, apply_result)
            except Exception as e:
                root.after(0, lambda e=e: append_log(f"解析失败: {e}", "error"))
            finally:
                root.after(0, lambda: parse_btn.configure(state="normal", text="解析列表"))
        threading.Thread(target=parse_thread, daemon=True).start()

    parse_btn.configure(command=do_parse)

    def do_get_cookie():
        append_log("正在获取 Cookie...")
        cookie_btn.configure(state="disabled", text="获取中...")
        set_cookie_status("获取中", PRIMARY, PRIMARY_SOFT)

        def cookie_thread():
            cookie = auto_get_bilibili_cookie(root_window=root)
            if cookie:
                is_valid, msg, _ = _validate_bilibili_cookie(cookie)
                username = msg.split("[")[1].split("]")[0] if "[" in msg else "已登录"
                available = state["available_qualities"]
                if state["parse_bvid"]:
                    available = fetch_bilibili_qualities(state["parse_bvid"], state["parse_cid"], cookie)
                def apply_cookie():
                    state["cookie"] = cookie
                    state["available_qualities"] = available
                    state["quality_checked"] = bool(state["parse_bvid"])
                    set_cookie_status(username if is_valid else "已获取 Cookie", SUCCESS, SUCCESS_SOFT)
                    append_log(f"Cookie 获取成功: {msg}", "success")
                    rebuild_quality_buttons()
                root.after(0, apply_cookie)
            else:
                root.after(0, lambda: set_cookie_status("未登录", DANGER, DANGER_SOFT))
                root.after(0, lambda: append_log("Cookie 获取失败，可能仅能下载低画质", "warning"))
                root.after(0, rebuild_quality_buttons)
            root.after(0, lambda: cookie_btn.configure(state="normal", text="获取 Cookie"))
        threading.Thread(target=cookie_thread, daemon=True).start()

    cookie_btn.configure(command=do_get_cookie)

    def do_clear_cache():
        _clear_cookie_cache()
        state["cookie"] = ""
        state["available_qualities"] = set()
        state["quality_checked"] = False
        set_cookie_status("未登录", TEXT_DIM, PANEL_SOFT)
        append_log("Cookie 缓存已清除，请重新解析以刷新未登录可用画质", "warning")
        rebuild_quality_buttons()

    clear_cache_btn.configure(command=do_clear_cache)

    def download_worker(task_id):
        info = state["tasks"].get(task_id)
        if not info:
            return
        control = {"pause_event": info["pause_event"], "cancel_event": info["cancel_event"]}
        def log(msg):
            root.after(0, lambda msg=msg: append_log(f"  [{info['seq']}] {msg}"))
        def progress(stage, percent=None, detail=""):
            task = state["tasks"].get(task_id)
            if not task:
                return
            task["stage"] = stage
            if percent is not None:
                task["status"] = "下载中"
                task["percent"] = percent
            root.after(0, lambda: refresh_task_row(task_id))
        success = download_bilibili_video(
            info["video_url"], cookie_str=info["cookie"], output_dir=info["out_dir"], logger=log,
            selected_option=info["selected_option"], control=control, progress_callback=progress,
            quality_id=info["quality_id"], want_video=info["want_video"], want_audio=info["want_audio"], cover_url=info["cover_url"]
        )
        def finish_task():
            task = state["tasks"].get(task_id)
            if task:
                if success:
                    task["status"] = "已完成"
                    task["percent"] = 100.0
                    append_log(f"  [{task['seq']}] 下载完成", "success")
                elif task["status"] != "已取消":
                    task["status"] = "失败"
                    append_log(f"  [{task['seq']}] 下载失败", "error")
                refresh_task_row(task_id)
            state["active_downloads"] = max(0, state["active_downloads"] - 1)
            maybe_start_next_download()
        root.after(0, finish_task)

    def start_download():
        selected_options = [item["option"] for item in video_option_map if item["var"].get()]
        if not selected_options:
            append_log("请先在左侧勾选要下载的视频", "warning")
            return
        want_video = download_video_var.get()
        want_audio = download_audio_var.get()
        want_cover = download_cover_var.get()
        if not want_video and not want_audio and not want_cover:
            append_log("请至少选择一种下载内容（视频 / 音频 / 封面）", "warning")
            return
        cover_url = state["cover_url"] if want_cover else None
        quality_id = state["quality_id"] if want_video else None
        out_dir = folder_var.get().strip() or default_dir
        if not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir)
            except Exception as e:
                append_log(f"无法创建目录: {e}", "error")
                return
        for selected_option in selected_options:
            task_id = uuid.uuid4().hex[:10]
            pause_event = threading.Event()
            pause_event.set()
            state["task_order"].append(task_id)
            state["tasks"][task_id] = {
                "seq": len(state["task_order"]), "title": selected_option.get("title", "未知"),
                "status": "等待中", "percent": 0.0, "stage": "等待调度",
                "pause_event": pause_event, "cancel_event": threading.Event(),
                "video_url": selected_option.get("url") or state.get("parsed_url", ""),
                "out_dir": out_dir, "cookie": state["cookie"], "selected_option": selected_option,
                "quality_id": quality_id, "want_video": want_video, "want_audio": want_audio, "cover_url": cover_url,
            }
            state["task_queue"].append(task_id)
            refresh_task_row(task_id)
        qname = QUALITY_NAME.get(quality_id, "默认")
        parts = []
        if want_video:
            parts.append(f"视频[{qname}]")
        if want_audio:
            parts.append("音频")
        if want_cover:
            parts.append("封面")
        append_log(f"已加入 {len(selected_options)} 个任务：{' + '.join(parts)}", "success")
        maybe_start_next_download()

    download_btn.configure(command=start_download)
    task_download_btn.configure(command=start_download)

    def pause_all():
        for tid in state["task_order"]:
            info = state["tasks"][tid]
            if info["status"] == "下载中":
                info["pause_event"].clear()
                info["status"] = "已暂停"
                refresh_task_row(tid)
        append_log("已暂停进行中的任务")

    def resume_all():
        for tid in state["task_order"]:
            info = state["tasks"][tid]
            if info["status"] == "已暂停":
                info["pause_event"].set()
                info["status"] = "下载中"
                refresh_task_row(tid)
        append_log("已继续任务")

    def cancel_all():
        for tid in state["task_order"]:
            info = state["tasks"][tid]
            if info["status"] not in ("已完成", "已取消", "失败"):
                info["cancel_event"].set()
                info["status"] = "已取消"
                if tid in state["task_queue"]:
                    state["task_queue"].remove(tid)
                refresh_task_row(tid)
        append_log("已发送取消信号")
        maybe_start_next_download()

    def clear_done():
        for tid in list(state["task_order"]):
            if state["tasks"][tid]["status"] in ("已完成", "已取消", "失败"):
                w = task_widgets.pop(tid, None)
                if w:
                    w["frame"].destroy()
                state["task_order"].remove(tid)
                state["tasks"].pop(tid, None)
                if tid in state["task_queue"]:
                    state["task_queue"].remove(tid)
        if not state["task_order"] and task_placeholder.winfo_exists():
            task_placeholder.pack(expand=True, fill="both", pady=42)
        update_task_count()
        append_log("已清除已结束的任务")

    pause_btn.configure(command=pause_all)
    resume_btn.configure(command=resume_all)
    cancel_btn.configure(command=cancel_all)
    clear_done_btn.configure(command=clear_done)

    set_cookie_status("未登录", TEXT_DIM, PANEL_SOFT)
    append_log("界面已就绪。")
    # 启动自检：ffmpeg 可用性（合成音视频必须）
    _ff_ok, _ff_msg = _check_ffmpeg()
    append_log(_ff_msg)
    if not _ff_ok:
        append_log("⚠️ 警告：缺少 ffmpeg，仅下载音频/封面可正常工作，视频合成会失败。")
    append_log("粘贴链接 -> 解析列表 -> 勾选视频与下载内容 -> 下载选中视频")
    append_log("提示：高画质需先获取 Cookie；仅音频/封面可单独下载。")

    root.mainloop()


if __name__ == "__main__":
    launch_gui()
