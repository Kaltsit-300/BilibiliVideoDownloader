"""
Bilibili Video Downloader - B站视频下载器
支持：高质量视频下载 / Cookie 自动获取（扫码登录 + 本地缓存） / 一键保存
"""

import requests
import json
import re
import os
import time
import sys
import threading
import uuid
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, Toplevel
from tkinter.scrolledtext import ScrolledText
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


# ============================================================
# Cookie 工具函数
# ============================================================

def _cookie_dict_to_header(cookie_dict):
    filtered = {k: v for k, v in cookie_dict.items() if v}
    return "; ".join(f"{k}={v}" for k, v in filtered.items())


def _is_cookie_usable(cookie_header):
    if not cookie_header:
        return False
    return sum(1 for k in REQUIRED_COOKIE_KEYS if f"{k}=" in cookie_header) >= 1


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

        # 尝试在 GUI 中显示二维码弹窗
        qr_window = None
        qr_label = None
        if root_window and Image:
            try:
                qr_img_data = requests.get(desktop_qr_url, timeout=15).content
                pil_image = Image.open(io.BytesIO(qr_img_data))
                tk_image = ImageTk.PhotoImage(pil_image)

                qr_window = Toplevel(root_window)
                qr_window.title("B站扫码登录")
                qr_window.geometry("360x420")
                qr_window.configure(bg="#ffffff")
                qr_window.resizable(False, False)
                qr_window.transient(root_window)
                qr_window.grab_set()

                tk.Label(
                    qr_window, text="请用 B站 App 扫描二维码",
                    bg="#ffffff", fg="#333333",
                    font=("Microsoft YaHei UI", 12, "bold")
                ).pack(pady=(20, 10))

                qr_label = tk.Label(qr_window, image=tk_image, bg="#ffffff")
                qr_label.image = tk_image  # keep reference
                qr_label.pack(padx=20, pady=10)

                tk.Label(
                    qr_window,
                    text="打开 B站App → 扫一扫 → 确认登录",
                    bg="#ffffff", fg="#666666",
                    font=("Microsoft YaHei UI", 9)
                ).pack(pady=(0, 15))

                status_lbl = tk.Label(
                    qr_window, text="等待扫码...",
                    bg="#ffffff", fg="#00a1d6",
                    font=("Microsoft YaHei UI", 10)
                )
                status_lbl.pack()

                def update_status(text, color="#00a1d6"):
                    status_lbl.configure(text=text, fg=color)
                    qr_window.update()

                qr_window._update_status = update_status

                # 居中显示
                qr_window.update_idletasks()
                x = root_window.winfo_x() + (root_window.winfo_width() - 360) // 2
                y = root_window.winfo_y() + (root_window.winfo_height() - 420) // 2
                qr_window.geometry(f"+{max(x,0)}+{max(y,0)}")

            except Exception as img_err:
                print(f"   - 二维码弹窗创建失败: {img_err}")
                print(f"   - 请在浏览器中打开: {desktop_qr_url}")
                qr_window = None
        else:
            print(f"\n请在浏览器打开下面链接，然后用手机 B站 App 扫码：")
            print(desktop_qr_url)

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

            if qr_window and hasattr(qr_window, "_update_status"):
                if status_code == 86090:
                    qr_window._update_status("✅ 扫码成功，请在手机上确认...", "#22c55e")
                elif status_code == 86038:
                    qr_window._update_status("❌ 二维码已失效", "#ef4444")

            if status_code == 0:
                callback_url = inner.get("url", "")
                cookie = _parse_cookie_from_callback_url(callback_url)
                if _is_cookie_usable(cookie):
                    print("   - ✅ 扫码登录成功，已获取可用 Cookie。")
                    if qr_window:
                        try:
                            qr_window._update_status("✅ 登录成功！", "#22c55e")
                            qr_window.update()
                            qr_window.after(1500, qr_window.destroy)
                        except Exception:
                            pass
                    return cookie
                print("   - 扫码成功但回调中未拿到完整 Cookie 字段。")
                if qr_window:
                    try:
                        qr_window.destroy()
                    except Exception:
                        pass
                return ""
            if status_code == 86038:
                print("   - 二维码已失效，请重试。")
                if qr_window:
                    try:
                        qr_window.destroy()
                    except Exception:
                        pass
                return ""

        print("   - 等待扫码超时。")
        if qr_window:
            try:
                qr_window.destroy()
            except Exception:
                pass
        return ""
    except Exception as e:
        print(f"   - 二维码登录失败: {e}")
        if qr_window:
            try:
                qr_window.destroy()
            except Exception:
                pass
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
# 视频下载核心逻辑
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
    state_match = re.search(r'window\.__INITIAL_STATE__=(.*?);\(function', html_data)
    if not state_match:
        return {}
    try:
        return json.loads(state_match.group(1))
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
    if not initial_state:
        raise ValueError("解析失败，未能读取页面视频数据。")

    page_title = _page_title_from_html(html_data)
    options = _extract_video_options(initial_state, bvid)
    if not options:
        raise ValueError("解析失败，未找到可下载的视频条目。")

    emit(f"解析完成：找到 {len(options)} 个可选视频。")
    return {
        "page_title": page_title,
        "source_url": final_url,
        "options": options,
    }


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


def download_bilibili_video(
    url, cookie_str="", output_dir=None, logger=None, selected_option=None,
    control=None, progress_callback=None
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
            emit(f"未手动选择条目，默认下载：{describe_video_option(selected_option)}")
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

    accept_quality = play_json.get("data", {}).get("accept_quality", [])
    if accept_quality:
        emit(f"账号可用画质档位ID: {accept_quality}")

    try:
        dash_data = play_json.get("data", {}).get("dash", {})
        video_list = dash_data.get('video', [])
        audio_list = dash_data.get('audio', [])
        if not video_list or not audio_list:
            emit("未拿到 DASH 音视频流，可能视频受限、链接无效或账号权限不足。")
            return False

        hevc_list = [v for v in video_list if v.get('codecid') == 12]
        av1_list = [v for v in video_list if v.get('codecid') == 13]
        avc_list = [v for v in video_list if v.get('codecid') == 7]

        if hevc_list:
            target_video_list, codec_name = hevc_list, "HEVC (H.265)"
        elif av1_list:
            target_video_list, codec_name = av1_list, "AV1"
        elif avc_list:
            target_video_list, codec_name = avc_list, "AVC (H.264)"
        else:
            target_video_list, codec_name = video_list, "未知编码"

        best_video = sorted(target_video_list, key=lambda x: (x['id'], x['bandwidth']), reverse=True)[0]
        video_url = best_video.get('baseUrl') or best_video.get('base_url')
        video_id = best_video['id']

        best_audio = sorted(audio_list, key=lambda x: x['id'], reverse=True)[0]
        audio_url = best_audio.get('baseUrl') or best_audio.get('base_url')
        if not video_url or not audio_url:
            emit("解析失败：未拿到可用的音视频下载地址。")
            return False

        quality_map = {
            127: "8K", 120: "4K", 116: "1080P 60帧", 112: "1080P 高码率",
            80: "1080P", 74: "720P 60帧", 64: "720P", 32: "480P", 16: "360P"
        }
        quality_str = quality_map.get(video_id, f"未知画质(ID:{video_id})")
        emit(f"嗅探成功：画质【{quality_str}】| 编码【{codec_name}】")

        if video_id <= 32:
            emit("警告：当前画质仅为 480P 或更低，可能是 Cookie 权限不足或已过期。")

    except KeyError:
        emit("解析失败，数据结构可能发生变化。")
        return False

    if not os.path.exists(desktop_path):
        os.makedirs(desktop_path)

    task_id = uuid.uuid4().hex[:10]
    video_temp = os.path.join(desktop_path, f".bili_video_{task_id}.m4s")
    audio_temp = os.path.join(desktop_path, f".bili_audio_{task_id}.m4s")
    output_filename = _unique_output_path(desktop_path, f"{title}.mp4")

    def download_file(download_url, filename, desc, base_percent, span_percent):
        emit(f"正在下载 {desc} ...")
        progress(desc, base_percent, "连接中")
        downloaded = 0
        last_percent = -1
        with requests.get(download_url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    _wait_if_paused(control)
                    _raise_if_cancelled(control)
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        inner = min(100, int(downloaded * 100 / total))
                        overall = base_percent + (inner * span_percent / 100)
                        if inner != last_percent:
                            last_percent = inner
                            progress(desc, overall, f"{inner}%")
        progress(desc, base_percent + span_percent, "完成")
        emit(f"{desc} 下载完成。")

    try:
        download_file(video_url, video_temp, "下载视频", 5, 45)
        download_file(audio_url, audio_temp, "下载音频", 50, 35)
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
        emit("下载与合成完成。")
        emit("提示：若默认播放器无法播放画面，请尝试 PotPlayer 或 VLC。")
        return True
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


# ============================================================
# GUI 界面（CustomTkinter 重构版）
# ============================================================

def launch_gui():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("Bilibili Video Downloader")
    root.geometry("1100x780")
    root.minsize(900, 680)

    state = {"cookie": "", "video_options": [], "parsed_url": "", "tasks": {}, "task_order": []}
    default_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    url_pattern = re.compile(r"https?://[^\s]+")

    # Variables
    cookie_status_var = ctk.StringVar(value="Cookie 未获取")
    url_var = ctk.StringVar(value="")
    folder_var = ctk.StringVar(value=default_dir)
    task_count_var = ctk.StringVar(value="准备就绪")

    # Color scheme - B站 inspired
    BILI_BLUE = "#00A1D6"
    BILI_PINK = "#FB7299"
    
    # ============================================================
    # Layout
    # ============================================================
    
    # Main container
    main_frame = ctk.CTkFrame(root, fg_color="transparent")
    main_frame.pack(fill="both", expand=True, padx=20, pady=(16, 12))

    # ---- Header ----
    header = ctk.CTkFrame(main_frame, fg_color="transparent", height=56)
    header.pack(fill="x", pady=(0, 16))

    title_frame = ctk.CTkFrame(header, fg_color="transparent")
    title_frame.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(
        title_frame, text="Bilibili", font=ctk.CTkFont(size=26, weight="bold"),
        text_color=BILI_PINK
    ).pack(side="left")
    ctk.CTkLabel(
        title_frame, text="Video Downloader", font=ctk.CTkFont(size=26, weight="bold"),
        text_color=("#1a1a1a", "#e0e0e0")
    ).pack(side="left", padx=(4, 0))
    ctk.CTkLabel(
        title_frame, text="合集多选 · 并行下载 · 扫码登录",
        font=ctk.CTkFont(size=11), text_color=("gray50", "gray40")
    ).pack(side="left", padx=(16, 0), pady=(8, 0))

    # Cookie status badge
    cookie_badge = ctk.CTkFrame(header, fg_color=("#dff5f7", "#1a3a3f"), corner_radius=20)
    cookie_badge.pack(side="right", padx=(10, 0))
    cookie_dot = ctk.CTkLabel(cookie_badge, text="●", font=ctk.CTkFont(size=14), text_color=("gray60", "gray50"))
    cookie_dot.pack(side="left", padx=(12, 4), pady=6)
    cookie_label = ctk.CTkLabel(
        cookie_badge, textvariable=cookie_status_var,
        font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray30", "gray70")
    )
    cookie_label.pack(side="left", padx=(0, 12), pady=6)

    def set_cookie_status(text, color):
        cookie_status_var.set(text)
        cookie_dot.configure(text_color=color)
        cookie_label.configure(text_color=color)

    # ---- Content: two columns ----
    content = ctk.CTkFrame(main_frame, fg_color="transparent")
    content.pack(fill="both", expand=True)
    left_col = tk.Frame(content, bg=colors["bg"], width=500)
    left_col.pack(side="left", fill="both", padx=(0, 18))
    left_col.pack_propagate(False)
    right_col = tk.Frame(content, bg=colors["bg"])
    right_col.pack(side="left", fill="both", expand=True)

    # Left column
    left_col = ctk.CTkFrame(content, fg_color="transparent")
    left_col.pack(side="left", fill="both", expand=True, padx=(0, 10))
    left_col.grid_columnconfigure(0, weight=1)

    # Right column
    right_col = ctk.CTkFrame(content, fg_color="transparent")
    right_col.pack(side="left", fill="both", expand=True, padx=(10, 0))
    right_col.grid_columnconfigure(0, weight=1)

    # ============================================================
    # LEFT: Source Card
    # ============================================================
    source_card = ctk.CTkFrame(left_col, corner_radius=12)
    source_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
    source_card.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        source_card, text="📥 下载来源", font=ctk.CTkFont(size=14, weight="bold")
    ).pack(anchor="w", padx=18, pady=(14, 12))

    # URL input
    url_label = ctk.CTkLabel(source_card, text="视频链接", font=ctk.CTkFont(size=11), text_color=("gray40", "gray50"))
    url_label.pack(anchor="w", padx=18, pady=(0, 4))
    url_entry = ctk.CTkEntry(
        source_card, placeholder_text="粘贴 B站视频链接…",
        font=ctk.CTkFont(size=12), height=38, border_width=1
    )
    url_entry.configure(textvariable=url_var)
    url_entry.pack(fill="x", padx=18, pady=(0, 10))

    # Folder
    folder_label = ctk.CTkLabel(source_card, text="下载目录", font=ctk.CTkFont(size=11), text_color=("gray40", "gray50"))
    folder_label.pack(anchor="w", padx=18, pady=(0, 4))
    folder_frame = ctk.CTkFrame(source_card, fg_color="transparent")
    folder_frame.pack(fill="x", padx=18, pady=(0, 6))
    folder_entry = ctk.CTkEntry(
        folder_frame, font=ctk.CTkFont(size=12), height=38, border_width=1
    )
    folder_entry.configure(textvariable=folder_var)
    folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    folder_btn = ctk.CTkButton(
        folder_frame, text="浏览", width=70, height=38,
        font=ctk.CTkFont(size=12), fg_color=("gray80", "gray30"),
        text_color=("gray15", "gray85"), hover_color=("gray70", "gray25"),
        border_width=0
    )

    def choose_folder():
        selected = filedialog.askdirectory(initialdir=folder_var.get() or default_dir)
        if selected:
            folder_var.set(selected)
    folder_btn.configure(command=choose_folder)
    folder_btn.pack(side="right")

    # Source buttons
    src_btn_row = ctk.CTkFrame(source_card, fg_color="transparent")
    src_btn_row.pack(fill="x", padx=18, pady=(4, 14))
    parse_btn = ctk.CTkButton(
        src_btn_row, text="🔍 解析列表", font=ctk.CTkFont(size=12, weight="bold"),
        height=34, fg_color=BILI_BLUE, hover_color="#0088b3", corner_radius=8,
        border_width=0
    )
    parse_btn.pack(side="left", padx=(0, 8))

    cookie_btn = ctk.CTkButton(
        src_btn_row, text="🍪 获取 Cookie", font=ctk.CTkFont(size=12, weight="bold"),
        height=34, fg_color=("gray75", "gray35"), text_color=("gray15", "gray85"),
        hover_color=("gray65", "gray25"), corner_radius=8, border_width=0
    )
    cookie_btn.pack(side="left", padx=(0, 8))

    action_bar = tk.Frame(selector_card, bg=colors["panel"])
    action_bar.pack(fill="x", pady=(12, 0))
    download_btn = create_modern_button(action_bar, "⬇ 下载选中视频", None, is_primary=True, width=18)
    download_btn.pack(side="left")
    clear_btn = create_modern_button(action_bar, "清除缓存", None, is_danger=True, width=11)
    clear_btn.pack(side="left", padx=(8, 0))

    # ---- Right: tasks and logs ----
    task_card = create_section(right_col, "下载任务", "选中任务后可暂停、继续或取消")
    task_body = tk.Frame(task_card, bg=colors["input_bg"], highlightthickness=1,
                         highlightbackground=colors["line_strong"], highlightcolor=colors["accent"])
    task_body.pack(fill="x")
    task_listbox = tk.Listbox(
        task_body, height=9, activestyle="none", exportselection=False, selectmode="extended",
        bg=colors["input_bg"], fg=colors["input_fg"], selectbackground=colors["accent"],
        selectforeground="#ffffff", relief="flat", font=mono_font, highlightthickness=0
    )
    task_listbox.pack(side="left", fill="x", expand=True, padx=(10, 0), pady=10)
    task_listbox.insert("end", "  ← 选择视频后，点击「⬇ 下载选中视频」或「⬇ 开始下载」")
    task_listbox.itemconfigure(0, fg=colors["dim"])
    task_scrollbar = tk.Scrollbar(task_body, orient="vertical", command=task_listbox.yview)
    task_scrollbar.pack(side="right", fill="y")
    task_listbox.configure(yscrollcommand=task_scrollbar.set)
    bind_local_scroll(task_listbox, task_listbox)
    bind_local_scroll(task_body, task_listbox)

    task_buttons = tk.Frame(task_card, bg=colors["panel"])
    task_buttons.pack(fill="x", pady=(12, 0))
    task_download_btn = create_modern_button(task_buttons, "⬇ 开始下载", None, is_primary=True, width=13)
    task_download_btn.pack(side="left")
    pause_btn = create_modern_button(task_buttons, "暂停", None, width=8)
    pause_btn.pack(side="left", padx=(8, 0))
    resume_btn = create_modern_button(task_buttons, "继续", None, width=8)
    resume_btn.pack(side="left", padx=(8, 0))
    cancel_btn = create_modern_button(task_buttons, "取消", None, is_danger=True, width=8)
    cancel_btn.pack(side="left", padx=(8, 0))

    list_header = ctk.CTkFrame(list_card, fg_color="transparent")
    list_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 8))
    ctk.CTkLabel(
        list_header, text="📋 视频列表", font=ctk.CTkFont(size=14, weight="bold")
    ).pack(side="left")
    list_count_label = ctk.CTkLabel(
        list_header, text="", font=ctk.CTkFont(size=11), text_color=("gray50", "gray50")
    )
    list_count_label.pack(side="right")

    # Listbox wrapped in CTkScrollableFrame
    list_wrap = ctk.CTkScrollableFrame(list_card, corner_radius=8, border_width=1,
                                        border_color=("gray80", "gray30"))
    list_wrap.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 8))
    list_wrap.grid_columnconfigure(0, weight=1)

    # Action bar under video list
    list_action_bar = ctk.CTkFrame(list_card, fg_color="transparent")
    list_action_bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(4, 14))
    download_btn = ctk.CTkButton(
        list_action_bar, text="⬇ 下载选中视频", font=ctk.CTkFont(size=12, weight="bold"),
        height=34, fg_color=BILI_PINK, hover_color="#e06080", corner_radius=8,
        border_width=0
    )
    download_btn.pack(side="left", padx=(0, 8))

    # ============================================================
    # RIGHT: Task Card
    # ============================================================
    task_card = ctk.CTkFrame(right_col, corner_radius=12)
    task_card.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
    task_card.grid_columnconfigure(0, weight=1)
    task_card.grid_rowconfigure(1, weight=1)
    right_col.grid_rowconfigure(0, weight=1)

    task_header = ctk.CTkFrame(task_card, fg_color="transparent")
    task_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 8))
    ctk.CTkLabel(
        task_header, text="📋 下载任务", font=ctk.CTkFont(size=14, weight="bold")
    ).pack(side="left")
    task_status_label = ctk.CTkLabel(
        task_header, textvariable=task_count_var, font=ctk.CTkFont(size=11),
        text_color=("gray50", "gray50")
    )
    task_status_label.pack(side="right")

    # Task scrollable area
    task_scroll = ctk.CTkScrollableFrame(task_card, corner_radius=8, border_width=1,
                                          border_color=("gray80", "gray30"))
    task_scroll.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 8))
    task_scroll.grid_columnconfigure(0, weight=1)

    # Placeholder for empty task list
    task_placeholder = ctk.CTkLabel(
        task_scroll, text="← 选择视频后点击下载按钮\n任务将显示在这里",
        font=ctk.CTkFont(size=11), text_color=("gray50", "gray40"), justify="center"
    )
    task_placeholder.pack(expand=True, fill="both", pady=40)

    # Task control buttons
    task_btn_row = ctk.CTkFrame(task_card, fg_color="transparent")
    task_btn_row.grid(row=2, column=0, sticky="ew", padx=18, pady=(4, 14))
    task_download_btn = ctk.CTkButton(
        task_btn_row, text="⬇ 开始下载", font=ctk.CTkFont(size=12, weight="bold"),
        height=32, fg_color=BILI_BLUE, hover_color="#0088b3", corner_radius=8,
        border_width=0
    )
    task_download_btn.pack(side="left", padx=(0, 6))
    pause_btn = ctk.CTkButton(
        task_btn_row, text="⏸", font=ctk.CTkFont(size=12), width=40, height=32,
        fg_color=("gray75", "gray35"), text_color=("gray15", "gray85"),
        hover_color=("gray65", "gray25"), corner_radius=8, border_width=0
    )
    pause_btn.pack(side="left", padx=(0, 4))
    resume_btn = ctk.CTkButton(
        task_btn_row, text="▶", font=ctk.CTkFont(size=12), width=40, height=32,
        fg_color=("gray75", "gray35"), text_color=("gray15", "gray85"),
        hover_color=("gray65", "gray25"), corner_radius=8, border_width=0
    )
    resume_btn.pack(side="left", padx=(0, 4))
    cancel_btn = ctk.CTkButton(
        task_btn_row, text="✕", font=ctk.CTkFont(size=14, weight="bold"), width=40, height=32,
        fg_color="#e04040", hover_color="#c03030", corner_radius=8, border_width=0
    )
    cancel_btn.pack(side="left", padx=(0, 6))
    clear_done_btn = ctk.CTkButton(
        task_btn_row, text="清除已完成", font=ctk.CTkFont(size=11),
        height=32, fg_color="transparent", text_color=("gray50", "gray50"),
        hover_color=("gray85", "gray25"), corner_radius=8, border_width=0
    )
    clear_done_btn.pack(side="right")

    # ============================================================
    # RIGHT: Log Card
    # ============================================================
    log_card = ctk.CTkFrame(right_col, corner_radius=12)
    log_card.grid(row=1, column=0, sticky="nsew", pady=(0, 0))
    log_card.grid_columnconfigure(0, weight=1)
    log_card.grid_rowconfigure(1, weight=1)
    right_col.grid_rowconfigure(1, weight=2)

    log_header = ctk.CTkFrame(log_card, fg_color="transparent")
    log_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 8))
    ctk.CTkLabel(
        log_header, text="📝 运行日志", font=ctk.CTkFont(size=14, weight="bold")
    ).pack(side="left")
    clear_log_btn = ctk.CTkButton(
        log_header, text="清空", font=ctk.CTkFont(size=10),
        width=50, height=24, fg_color="transparent", text_color=("gray50", "gray50"),
        hover_color=("gray85", "gray25"), corner_radius=6, border_width=0
    )
    clear_log_btn.pack(side="right")

    log_textbox = ctk.CTkTextbox(
        log_card, font=ctk.CTkFont(family="Consolas", size=11),
        corner_radius=8, border_width=1, border_color=("gray80", "gray30"),
        activate_scrollbars=True, wrap="word"
    )
    log_textbox.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 14))

    def _tag_color(light, dark):
        return dark if ctk.get_appearance_mode() == "dark" else light

    log_textbox.tag_config("success", foreground=_tag_color("#168a4a", "#7ee2a8"))
    log_textbox.tag_config("warning", foreground=_tag_color("#b7791f", "#f6c96b"))
    log_textbox.tag_config("error", foreground=_tag_color("#d83a34", "#ff8b86"))
    log_textbox.tag_config("hyperlink", foreground=_tag_color("#00A1D6", "#4dd0e1"), underline=True)

    # ============================================================
    # Bottom status
    # ============================================================
    status_bar = ctk.CTkFrame(main_frame, fg_color="transparent", height=24)
    status_bar.pack(fill="x", pady=(8, 4))
    status_label = ctk.CTkLabel(
        status_bar, textvariable=task_count_var,
        font=ctk.CTkFont(size=11), text_color=("gray50", "gray40")
    )
    status_label.pack(side="left")
    ctk.CTkLabel(
        status_bar, text="Bilibili Video Downloader v2.0",
        font=ctk.CTkFont(size=10), text_color=("gray60", "gray50")
    ).pack(side="right")

    # ============================================================
    # Task management data
    # ============================================================
    # Each task widget is stored: task_widgets[task_id] = {frame, progress, status_label, title_label, pause_event, cancel_event}
    task_widgets = {}

    def append_log(msg, tag=None):
        text = str(msg)
        log_textbox.configure(state="normal")
        pos = 0
        for match in url_pattern.finditer(text):
            if match.start() > pos:
                log_textbox.insert("end", text[pos:match.start()], tag or ())
            url_link = match.group(0)
            log_textbox.insert("end", url_link, "hyperlink")
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

    # ============================================================
    # Video list management
    # ============================================================
    video_checkboxes = []
    video_option_map = []

    def refresh_video_list(options):
        for w in video_checkboxes:
            w.destroy()
        video_checkboxes.clear()
        video_option_map.clear()

        if not options:
            placeholder = ctk.CTkLabel(
                list_wrap, text="← 点击「解析列表」加载视频",
                font=ctk.CTkFont(size=11), text_color=("gray50", "gray40"), justify="center"
            )
            placeholder.pack(expand=True, fill="both", pady=30)
            video_checkboxes.append(placeholder)
            list_count_label.configure(text="")
            return

        list_count_label.configure(text=f"共 {len(options)} 个视频")
        for opt in options:
            section_tag = f"[{opt['section']}] " if opt.get("section") else ""
            duration_tag = f"  {opt['duration']}" if opt.get("duration") else ""
            label_text = f"{opt['index']:02d}. {section_tag}{opt['title']}{duration_tag}"

            row = ctk.CTkFrame(list_wrap, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            row.grid_columnconfigure(0, weight=0)
            row.grid_columnconfigure(1, weight=1)

            var = ctk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(
                row, text="", variable=var, width=20, height=20,
                checkbox_width=20, checkbox_height=20, border_width=2,
                corner_radius=4
            )
            cb.grid(row=0, column=0, padx=(4, 8), pady=4, sticky="w")

            title_lbl = ctk.CTkLabel(
                row, text=label_text, font=ctk.CTkFont(size=11),
                anchor="w", justify="left"
            )
            title_lbl.grid(row=0, column=1, sticky="ew", pady=4)

            video_checkboxes.append(row)
            video_option_map.append({"var": var, "option": opt})

    # Initial placeholder
    refresh_video_list([])

    # ============================================================
    # Parsing
    # ============================================================
    def do_parse():
        url = url_var.get().strip()
        if not url:
            append_log("⚠️ 请先输入视频链接", "warning")
            return
        append_log(f"🔍 正在解析: {url}")
        parse_btn.configure(state="disabled", text="解析中…")
        root.update()

        def parse_thread():
            try:
                result = fetch_bilibili_video_options(
                    url, cookie_str=state["cookie"],
                    logger=lambda m: root.after(0, lambda: append_log("  " + m))
                )
                state["video_options"] = result["options"]
                state["parsed_url"] = result["source_url"]
                root.after(0, lambda: refresh_video_list(result["options"]))
                root.after(0, lambda: append_log(f"✅ 解析完成：找到 {len(result['options'])} 个视频", "success"))
            except Exception as e:
                root.after(0, lambda: append_log(f"❌ 解析失败: {e}", "error"))
            finally:
                root.after(0, lambda: [parse_btn.configure(state="normal", text="🔍 解析列表")])

        threading.Thread(target=parse_thread, daemon=True).start()

    parse_btn.configure(command=do_parse)

    # ============================================================
    # Cookie
    # ============================================================
    def do_get_cookie():
        append_log("🍪 正在获取 Cookie...")
        cookie_btn.configure(state="disabled", text="获取中…")
        root.update()

        def cookie_thread():
            cookie = auto_get_bilibili_cookie(root_window=root)
            if cookie:
                state["cookie"] = cookie
                is_valid, msg, _ = _validate_bilibili_cookie(cookie)
                username = msg.split("[")[1].split("]")[0] if "[" in msg else "已登录"
                root.after(0, lambda: set_cookie_status(f"✅ {username}", "#22c55e"))
                root.after(0, lambda: append_log(f"✅ Cookie 获取成功: {msg}", "success"))
            else:
                root.after(0, lambda: set_cookie_status("⚠️ Cookie 未获取", "#e04040"))
                root.after(0, lambda: append_log("⚠️ Cookie 获取失败，可能仅低画质", "warning"))
            root.after(0, lambda: cookie_btn.configure(state="normal", text="🍪 获取 Cookie"))

        threading.Thread(target=cookie_thread, daemon=True).start()

    cookie_btn.configure(command=do_get_cookie)

    def do_clear_cache():
        _clear_cookie_cache()
        state["cookie"] = ""
        set_cookie_status("Cookie 已清除", "gray50")
        append_log("🗑️ Cookie 缓存已清除", "warning")
    clear_cache_btn.configure(command=do_clear_cache)

    # ============================================================
    # Task widgets in scroll area
    # ============================================================
    def update_task_count():
        total = len(state["task_order"])
        active = sum(1 for tid in state["task_order"] if state["tasks"][tid]["status"] not in ("已完成", "已取消"))
        done = total - active
        if total == 0:
            task_count_var.set("准备就绪")
        else:
            task_count_var.set(f"已完成 {done}/{total}  · 进行中 {active}")

    def refresh_task_row(task_id):
        info = state["tasks"].get(task_id)
        if not info:
            return
        order = state["task_order"]
        # 首次添加任务时，清除占位提示
        if not order and task_listbox.size() == 1:
            first_text = task_listbox.get(0)
            if "选择视频后" in first_text:
                task_listbox.delete(0)
        if task_id not in order:
            order.append(task_id)
            task_listbox.insert("end", task_line(task))
        else:
            # Create new task widget
            placeholder = task_placeholder
            if placeholder.winfo_exists():
                try:
                    placeholder.pack_forget()
                except Exception:
                    pass

            frame = ctk.CTkFrame(task_scroll, fg_color="transparent", height=52)
            frame.pack(fill="x", padx=4, pady=3)
            frame.grid_columnconfigure(1, weight=1)
            frame.pack_propagate(False)

            title_lbl = ctk.CTkLabel(
                frame, text=info["title"],
                font=ctk.CTkFont(size=11, weight="bold"), anchor="w"
            )
            title_lbl.grid(row=0, column=0, columnspan=2, sticky="w", pady=(2, 0))

            prog = ctk.CTkProgressBar(frame, height=10, corner_radius=4,
                                       border_width=0, progress_color=("#00A1D6", "#4dd0e1"))
            prog.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 4))
            prog.set(0.0)

            status_lbl = ctk.CTkLabel(
                frame, text="等待中", font=ctk.CTkFont(size=10),
                text_color=("gray50", "gray50"), width=110, anchor="e"
            )
            status_lbl.grid(row=1, column=1, sticky="e", pady=(4, 4))

            task_widgets[task_id] = {
                "frame": frame, "progress": prog, "status_label": status_lbl
            }

        update_task_count()

    def download_worker(task_id, video_url, out_dir, cookie_str, selected_option):
        pause_event = state["tasks"][task_id]["pause_event"]
        cancel_event = state["tasks"][task_id]["cancel_event"]
        control = {"pause_event": pause_event, "cancel_event": cancel_event}

        def log(msg):
            root.after(0, lambda: append_log(f"  [{state['tasks'][task_id]['seq']}] {msg}"))

        def progress(stage, percent=None, detail=""):
            info = state["tasks"].get(task_id)
            if not info:
                return
            info["stage"] = stage
            info["status"] = "下载中" if percent is not None else info["status"]
            if percent is not None:
                info["percent"] = percent
            root.after(0, lambda: refresh_task_row(task_id))

        success = download_bilibili_video(
            video_url, cookie_str=cookie_str, output_dir=out_dir,
            logger=log, selected_option=selected_option,
            control=control, progress_callback=progress
        )

        if task_id in state["tasks"]:
            if success:
                state["tasks"][task_id]["status"] = "已完成"
                state["tasks"][task_id]["percent"] = 100.0
                root.after(0, lambda: append_log(f"  ✅ [{state['tasks'][task_id]['seq']}] 下载完成", "success"))
            else:
                if state["tasks"][task_id]["status"] != "已取消":
                    state["tasks"][task_id]["status"] = "失败"
                    root.after(0, lambda: append_log(f"  ❌ [{state['tasks'][task_id]['seq']}] 下载失败", "error"))
            root.after(0, lambda: refresh_task_row(task_id))

    def start_download():
        selected_options = []
        for item in video_option_map:
            if item["var"].get():
                selected_options.append(item["option"])

        if not selected_options:
            append_log("⚠️ 请先在左侧勾选要下载的视频", "warning")
            return

        out_dir = folder_var.get().strip() or default_dir
        if not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir)
            except Exception as e:
                append_log(f"❌ 无法创建目录: {e}", "error")
                return

        for selected_option in selected_options:
            task_id = uuid.uuid4().hex[:10]
            title = selected_option.get("title", "未知")
            video_url = selected_option.get("url") or state.get("parsed_url", "")
            pause_event = threading.Event()
            pause_event.set()  # not paused initially

            state["task_order"].append(task_id)
            state["tasks"][task_id] = {
                "seq": len(state["task_order"]),
                "title": title,
                "status": "等待中",
                "percent": 0.0,
                "stage": "",
                "pause_event": pause_event,
                "cancel_event": threading.Event(),
            }
            refresh_task_row(task_id)
            threading.Thread(
                target=download_worker,
                args=(task_id, video_url, out_dir, state["cookie"], selected_option),
                daemon=True
            ).start()

        append_log(f"🚀 已启动 {len(selected_options)} 个下载任务。", "success")

    download_btn.configure(command=start_download)
    task_download_btn.configure(command=start_download)

    append_log("界面已就绪。合集或分P视频请先点击「解析列表」，选择后点击「⬇ 下载选中视频」或右侧「⬇ 开始下载」即可。")
    append_log("提示：按住 Ctrl 可多选条目并行下载。若 Dolby Vision/HDR 发灰，请切换播放器渲染器。")

    root.mainloop()


if __name__ == "__main__":
    launch_gui()
