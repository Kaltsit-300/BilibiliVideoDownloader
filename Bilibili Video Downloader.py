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
# GUI 界面
# ============================================================

def launch_gui():
    root = tk.Tk()
    root.title("Bilibili Video Downloader")
    root.geometry("1180x820")
    root.minsize(960, 680)

    state = {"cookie": "", "video_options": [], "parsed_url": "", "tasks": {}, "task_order": []}
    default_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    url_pattern = re.compile(r"https?://[^\s]+")
    link_counter = {"n": 0}
    font_family = "Microsoft YaHei UI"
    mono_font = ("Consolas", 10)

    colors = {
        "bg": "#f4f1ea",
        "panel": "#ffffff",
        "panel_alt": "#f8f6f1",
        "line": "#ded6c9",
        "line_strong": "#c8bca9",
        "text": "#24211d",
        "muted": "#746b5f",
        "dim": "#9a9185",
        "accent": "#0a9fb5",
        "accent_hover": "#087f91",
        "accent_soft": "#dff5f7",
        "success": "#168a4a",
        "warning": "#b7791f",
        "danger": "#d83a34",
        "danger_hover": "#b62d29",
        "input_bg": "#fbfaf7",
        "input_fg": "#24211d",
        "log_bg": "#1f211f",
        "log_text": "#e8e2d8",
    }

    cookie_status_var = tk.StringVar(value="Cookie 未获取")
    folder_var = tk.StringVar(value=default_dir)
    url_var = tk.StringVar(value="")
    selected_video_var = tk.StringVar(value="未解析列表，将按当前链接下载。按住 Ctrl 可多选视频。")

    root.configure(bg=colors["bg"])

    # ---- Scrollable page shell ----
    page_canvas = tk.Canvas(root, bg=colors["bg"], highlightthickness=0)
    page_scrollbar = tk.Scrollbar(root, orient="vertical", command=page_canvas.yview)
    page_canvas.configure(yscrollcommand=page_scrollbar.set)
    page_canvas.pack(side="left", fill="both", expand=True)
    page_scrollbar.pack(side="right", fill="y")

    page = tk.Frame(page_canvas, bg=colors["bg"])
    page_window = page_canvas.create_window((0, 0), window=page, anchor="nw")

    def wheel_units(event):
        if event.delta:
            return int(-1 * (event.delta / 120))
        return 0

    def scroll_page(event):
        units = wheel_units(event)
        if units:
            page_canvas.yview_scroll(units, "units")
        return "break"

    def bind_local_scroll(widget, target):
        def _scroll(event):
            units = wheel_units(event)
            if units:
                target.yview_scroll(units, "units")
            return "break"
        widget.bind("<MouseWheel>", _scroll)

    def _sync_scroll_region(_event=None):
        page_canvas.configure(scrollregion=page_canvas.bbox("all"))

    def _sync_page_width(event):
        page_canvas.itemconfigure(page_window, width=event.width)

    page.bind("<Configure>", _sync_scroll_region)
    page_canvas.bind("<Configure>", _sync_page_width)
    root.bind_all("<MouseWheel>", scroll_page)

    shell = tk.Frame(page, bg=colors["bg"], padx=28, pady=24)
    shell.pack(fill="both", expand=True)

    def create_section(parent, title, subtitle=None):
        card = tk.Frame(parent, bg=colors["panel"], padx=18, pady=16,
                        highlightthickness=1, highlightbackground=colors["line"])
        card.pack(fill="x", pady=(0, 16))
        header = tk.Frame(card, bg=colors["panel"])
        header.pack(fill="x", pady=(0, 12))
        tk.Label(
            header, text=title, bg=colors["panel"], fg=colors["text"],
            font=(font_family, 13, "bold"), anchor="w"
        ).pack(side="left")
        if subtitle:
            tk.Label(
                header, text=subtitle, bg=colors["panel"], fg=colors["muted"],
                font=(font_family, 9), anchor="e"
            ).pack(side="right")
        return card

    def make_entry(parent, variable, font_size=10):
        return tk.Entry(
            parent, textvariable=variable,
            bg=colors["input_bg"], fg=colors["input_fg"],
            insertbackground=colors["input_fg"], relief="flat",
            font=(font_family, font_size), bd=0,
            highlightthickness=1, highlightcolor=colors["accent"],
            highlightbackground=colors["line_strong"]
        )

    def create_modern_button(parent, text, command, is_primary=False, is_danger=False, width=None):
        if is_primary:
            normal_bg, hover_bg, fg = colors["accent"], colors["accent_hover"], "#ffffff"
        elif is_danger:
            normal_bg, hover_bg, fg = colors["danger"], colors["danger_hover"], "#ffffff"
        else:
            normal_bg, hover_bg, fg = colors["panel_alt"], "#ebe5dc", colors["text"]

        btn = tk.Button(
            parent, text=text, command=command,
            bg=normal_bg, fg=fg, relief="flat",
            activebackground=hover_bg, activeforeground=fg,
            cursor="hand2", font=(font_family, 10, "bold"),
            padx=14, pady=8, bd=0, width=width
        )
        btn.bind("<Enter>", lambda _e, b=btn, h=hover_bg: b.configure(bg=h))
        btn.bind("<Leave>", lambda _e, b=btn, n=normal_bg: b.configure(bg=n))
        return btn

    def labeled_block(parent, label_text):
        block = tk.Frame(parent, bg=colors["panel"])
        block.pack(fill="x", pady=(0, 12))
        tk.Label(
            block, text=label_text, bg=colors["panel"], fg=colors["muted"],
            font=(font_family, 9, "bold"), anchor="w"
        ).pack(fill="x", pady=(0, 5))
        return block

    # ---- Header ----
    header = tk.Frame(shell, bg=colors["bg"])
    header.pack(fill="x", pady=(0, 18))
    title_area = tk.Frame(header, bg=colors["bg"])
    title_area.pack(side="left", fill="x", expand=True)
    tk.Label(
        title_area, text="Bilibili Video Downloader", bg=colors["bg"],
        fg=colors["text"], font=(font_family, 24, "bold"), anchor="w"
    ).pack(anchor="w")
    tk.Label(
        title_area, text="合集多选、并行下载、进度控制和扫码登录",
        bg=colors["bg"], fg=colors["muted"], font=(font_family, 10), anchor="w"
    ).pack(anchor="w", pady=(5, 0))

    status_pill = tk.Frame(header, bg=colors["accent_soft"], padx=14, pady=8,
                           highlightthickness=1, highlightbackground="#bfe7eb")
    status_pill.pack(side="right", anchor="n", padx=(18, 0))
    status_dot = tk.Canvas(status_pill, width=10, height=10, bg=colors["accent_soft"], highlightthickness=0)
    status_dot.create_oval(1, 1, 9, 9, fill=colors["dim"], outline="")
    status_dot.pack(side="left", padx=(0, 8))
    status_label = tk.Label(
        status_pill, textvariable=cookie_status_var, bg=colors["accent_soft"],
        fg=colors["muted"], font=(font_family, 10, "bold"), anchor="w"
    )
    status_label.pack(side="left")

    def set_cookie_status(text, color):
        cookie_status_var.set(text)
        status_label.configure(fg=color)
        status_dot.delete("all")
        status_dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    # ---- Two-column content ----
    content = tk.Frame(shell, bg=colors["bg"])
    content.pack(fill="both", expand=True)
    left_col = tk.Frame(content, bg=colors["bg"], width=470)
    left_col.pack(side="left", fill="both", padx=(0, 18))
    left_col.pack_propagate(False)
    right_col = tk.Frame(content, bg=colors["bg"])
    right_col.pack(side="left", fill="both", expand=True)

    # ---- Left: source and video selection ----
    input_card = create_section(left_col, "下载来源")

    url_block = labeled_block(input_card, "视频链接")
    url_entry = make_entry(url_block, url_var, font_size=10)
    url_entry.pack(fill="x", ipady=8)

    folder_block = labeled_block(input_card, "下载目录")
    folder_entry = make_entry(folder_block, folder_var)
    folder_entry.pack(fill="x", ipady=8)

    def choose_folder():
        selected = filedialog.askdirectory(initialdir=folder_var.get() or default_dir)
        if selected:
            folder_var.set(selected)

    source_buttons = tk.Frame(input_card, bg=colors["panel"])
    source_buttons.pack(fill="x", pady=(2, 0))
    parse_btn = create_modern_button(source_buttons, "解析列表", None, is_primary=True, width=11)
    parse_btn.pack(side="left")
    cookie_btn = create_modern_button(source_buttons, "获取 Cookie", None, width=11)
    cookie_btn.pack(side="left", padx=(8, 0))
    folder_btn = create_modern_button(source_buttons, "选择文件夹", choose_folder, width=11)
    folder_btn.pack(side="left", padx=(8, 0))

    selector_card = create_section(left_col, "选择视频", "按住 Ctrl 可多选")
    selector_hint_box = tk.Frame(selector_card, bg=colors["panel"], height=34)
    selector_hint_box.pack(fill="x", pady=(0, 8))
    selector_hint_box.pack_propagate(False)
    selector_hint = tk.Label(
        selector_hint_box, textvariable=selected_video_var, bg=colors["panel"],
        fg=colors["muted"], font=(font_family, 9), anchor="w", justify="left"
    )
    selector_hint.pack(fill="x", expand=True)

    list_wrap = tk.Frame(selector_card, bg=colors["input_bg"], highlightthickness=1,
                         highlightbackground=colors["line_strong"], highlightcolor=colors["accent"])
    list_wrap.pack(fill="both", expand=True)
    video_listbox = tk.Listbox(
        list_wrap, height=15, activestyle="none", exportselection=False, selectmode="extended",
        bg=colors["input_bg"], fg=colors["input_fg"], selectbackground=colors["accent"],
        selectforeground="#ffffff", relief="flat", font=(font_family, 10), highlightthickness=0
    )
    video_listbox.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
    video_scrollbar = tk.Scrollbar(list_wrap, orient="vertical", command=video_listbox.yview)
    video_scrollbar.pack(side="right", fill="y")
    video_listbox.configure(yscrollcommand=video_scrollbar.set)
    bind_local_scroll(video_listbox, video_listbox)
    bind_local_scroll(list_wrap, video_listbox)

    action_bar = tk.Frame(selector_card, bg=colors["panel"])
    action_bar.pack(fill="x", pady=(12, 0))
    download_btn = create_modern_button(action_bar, "下载选中视频", None, is_primary=True, width=16)
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
    task_scrollbar = tk.Scrollbar(task_body, orient="vertical", command=task_listbox.yview)
    task_scrollbar.pack(side="right", fill="y")
    task_listbox.configure(yscrollcommand=task_scrollbar.set)
    bind_local_scroll(task_listbox, task_listbox)
    bind_local_scroll(task_body, task_listbox)

    task_buttons = tk.Frame(task_card, bg=colors["panel"])
    task_buttons.pack(fill="x", pady=(12, 0))
    pause_btn = create_modern_button(task_buttons, "暂停", None, width=9)
    pause_btn.pack(side="left")
    resume_btn = create_modern_button(task_buttons, "继续", None, width=9)
    resume_btn.pack(side="left", padx=(8, 0))
    cancel_btn = create_modern_button(task_buttons, "取消", None, is_danger=True, width=9)
    cancel_btn.pack(side="left", padx=(8, 0))

    log_card = create_section(right_col, "运行日志", "日志区内滚动不会带动页面")
    log_text = ScrolledText(
        log_card, height=20, wrap="word", state="disabled",
        bg=colors["log_bg"], fg=colors["log_text"], insertbackground=colors["log_text"],
        relief="flat", font=mono_font, padx=12, pady=10,
        highlightthickness=1, highlightbackground=colors["line_strong"], highlightcolor=colors["accent"]
    )
    log_text.pack(fill="both", expand=True)
    log_text.tag_configure("hyperlink", foreground="#4dd0e1", underline=True)
    log_text.tag_bind("hyperlink", "<Enter>", lambda _e: log_text.config(cursor="hand2"))
    log_text.tag_bind("hyperlink", "<Leave>", lambda _e: log_text.config(cursor="arrow"))
    log_text.tag_configure("success", foreground="#7ee2a8")
    log_text.tag_configure("warning", foreground="#f6c96b")
    log_text.tag_configure("error", foreground="#ff8b86")
    bind_local_scroll(log_text, log_text)

    footer_space = tk.Frame(shell, bg=colors["bg"], height=16)
    footer_space.pack(fill="x")

    # ---- Logging ----
    def append_log(msg, tag=None):
        def _write():
            log_text.configure(state="normal")
            text = str(msg)
            pos = 0
            for match in url_pattern.finditer(text):
                if match.start() > pos:
                    log_text.insert("end", text[pos:match.start()])
                url_link = match.group(0)
                tag_name = f"link_{link_counter['n']}"
                link_counter["n"] += 1
                log_text.insert("end", url_link, ("hyperlink", tag_name))
                log_text.tag_bind(tag_name, "<Button-1>", lambda _e, u=url_link: webbrowser.open(u))
                pos = match.end()
            if pos < len(text):
                log_text.insert("end", text[pos:], tag)
            log_text.insert("end", "\n")
            log_text.see("end")
            log_text.configure(state="disabled")
        if threading.current_thread() is threading.main_thread():
            _write()
        else:
            root.after(0, _write)

    class _LogRedirect(io.TextIOBase):
        def __init__(self, writer):
            self.writer = writer
            self._buffer = ""
        def write(self, s):
            if not s:
                return 0
            self._buffer += s
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self.writer(line)
            return len(s)
        def flush(self):
            if self._buffer.strip():
                self.writer(self._buffer.strip())
            self._buffer = ""

    # ---- Video selection actions ----
    def compact_text(text, limit=46):
        text = str(text or "").replace("\n", " ").strip()
        return text if len(text) <= limit else text[:limit - 1] + "…"

    def selected_video_summary(option):
        section = f"[{option.get('section')}] " if option.get("section") else ""
        duration = f"  {option.get('duration')}" if option.get("duration") else ""
        return f"{option.get('index'):02d}. {compact_text(section + option.get('title', ''), 34)}{duration}"

    def set_selected_video_text(text):
        selected_video_var.set(compact_text(text, 58))

    def set_video_options(options, parsed_url):
        state["video_options"] = options
        state["parsed_url"] = parsed_url
        video_listbox.delete(0, "end")
        for option in options:
            video_listbox.insert("end", describe_video_option(option))
        if options:
            video_listbox.selection_set(0)
            video_listbox.activate(0)
            set_selected_video_text(f"已选 1 个：{selected_video_summary(options[0])}。按住 Ctrl 可继续多选。")
        else:
            set_selected_video_text("未找到可选视频。")

    def clear_video_options():
        state["video_options"] = []
        state["parsed_url"] = ""
        video_listbox.delete(0, "end")
        set_selected_video_text("未解析列表，将按当前链接下载。按住 Ctrl 可多选视频。")

    def on_video_select(_event=None):
        selection = video_listbox.curselection()
        if not selection:
            set_selected_video_text("未选择视频。按住 Ctrl 可多选视频。")
            return
        options = state.get("video_options", [])
        if len(selection) == 1 and selection[0] < len(options):
            set_selected_video_text(f"已选 1 个：{selected_video_summary(options[selection[0]])}。按住 Ctrl 可继续多选。")
        else:
            set_selected_video_text(f"已选 {len(selection)} 个视频。按住 Ctrl 可增减选择。")

    video_listbox.bind("<<ListboxSelect>>", on_video_select)

    def parse_list_worker(video_url, cookie):
        append_log("-" * 60)
        try:
            parsed = fetch_bilibili_video_options(video_url, cookie_str=cookie, logger=append_log)
            root.after(0, lambda: set_video_options(parsed["options"], video_url))
            append_log("视频列表解析完成，请在列表中选择要下载的条目。", "success")
        except Exception as e:
            root.after(0, clear_video_options)
            append_log(f"视频列表解析失败: {e}", "error")
        finally:
            root.after(0, lambda: parse_btn.config(state="normal"))

    def parse_list_action():
        video_url = url_var.get().strip()
        if not video_url:
            messagebox.showwarning("提示", "请先填写 B站视频链接。")
            return
        parse_btn.config(state="disabled")
        clear_video_options()
        threading.Thread(target=parse_list_worker, args=(video_url, state["cookie"]), daemon=True).start()

    parse_btn.configure(command=parse_list_action)

    def on_url_change(*_args):
        if state.get("parsed_url") and url_var.get().strip() != state.get("parsed_url"):
            clear_video_options()

    url_var.trace_add("write", on_url_change)

    # ---- Cookie actions ----
    def get_cookie_worker():
        append_log("开始获取 B站 Cookie ...")
        redirector = _LogRedirect(append_log)
        with contextlib.redirect_stdout(redirector), contextlib.redirect_stderr(redirector):
            cookie = auto_get_bilibili_cookie(root_window=root)
            redirector.flush()
        if cookie:
            state["cookie"] = cookie
            hit_keys = [k for k in REQUIRED_COOKIE_KEYS if f"{k}=" in cookie]
            root.after(0, lambda: set_cookie_status(f"Cookie 已获取：{', '.join(hit_keys)}", colors["success"]))
            append_log("Cookie 获取成功。", "success")
        else:
            root.after(0, lambda: set_cookie_status("Cookie 获取失败", colors["danger"]))
            append_log("Cookie 获取失败。", "error")
            append_log("提示：点击「获取 Cookie」后弹出二维码，用 B站App 扫码即可。")
        root.after(0, lambda: cookie_btn.config(state="normal"))

    def get_cookie_action():
        cookie_btn.config(state="disabled")
        threading.Thread(target=get_cookie_worker, daemon=True).start()

    cookie_btn.configure(command=get_cookie_action)

    def clear_cache_action():
        _clear_cookie_cache()
        state["cookie"] = ""
        set_cookie_status("Cookie 已清除", colors["muted"])
        append_log("Cookie 缓存已清除。下次下载需重新扫码获取。", "warning")

    clear_btn.configure(command=clear_cache_action)

    # ---- Task actions ----
    def task_line(task):
        percent = task.get("percent")
        percent_text = f"{percent:5.1f}%" if isinstance(percent, (int, float)) else "  ---%"
        return f"{task['seq']:02d}  {percent_text}  {task['status']:<8}  {task['title']}"

    def refresh_task_row(task_id):
        task = state["tasks"].get(task_id)
        if not task:
            return
        order = state["task_order"]
        if task_id not in order:
            order.append(task_id)
            task_listbox.insert("end", task_line(task))
        else:
            idx = order.index(task_id)
            selected_ids = [order[i] for i in task_listbox.curselection() if i < len(order)]
            task_listbox.delete(idx)
            task_listbox.insert(idx, task_line(task))
            for i, existing_id in enumerate(order):
                if existing_id in selected_ids:
                    task_listbox.selection_set(i)

    def update_task(task_id, status=None, percent=None, detail=None):
        def _apply():
            task = state["tasks"].get(task_id)
            if not task:
                return
            if status is not None:
                task["status"] = status
            if percent is not None:
                task["percent"] = max(0, min(100, float(percent)))
            if detail:
                task["detail"] = detail
            refresh_task_row(task_id)
        root.after(0, _apply)

    def selected_task_ids():
        return [
            state["task_order"][i]
            for i in task_listbox.curselection()
            if i < len(state["task_order"])
        ]

    def pause_selected_tasks():
        ids = selected_task_ids()
        if not ids:
            messagebox.showwarning("提示", "请先在下载任务列表中选择任务。")
            return
        for task_id in ids:
            task = state["tasks"].get(task_id)
            if task and task["status"] not in {"完成", "失败", "已取消"}:
                task["pause_event"].clear()
                update_task(task_id, status="已暂停")
        append_log(f"已暂停 {len(ids)} 个任务。", "warning")

    def resume_selected_tasks():
        ids = selected_task_ids()
        if not ids:
            messagebox.showwarning("提示", "请先在下载任务列表中选择任务。")
            return
        for task_id in ids:
            task = state["tasks"].get(task_id)
            if task and task["status"] not in {"完成", "失败", "已取消"}:
                task["pause_event"].set()
                update_task(task_id, status="继续中")
        append_log(f"已继续 {len(ids)} 个任务。", "success")

    def cancel_selected_tasks():
        ids = selected_task_ids()
        if not ids:
            messagebox.showwarning("提示", "请先在下载任务列表中选择任务。")
            return
        for task_id in ids:
            task = state["tasks"].get(task_id)
            if task and task["status"] not in {"完成", "失败", "已取消"}:
                task["cancel_event"].set()
                task["pause_event"].set()
                update_task(task_id, status="取消中")
        append_log(f"已请求取消 {len(ids)} 个任务。", "warning")

    pause_btn.configure(command=pause_selected_tasks)
    resume_btn.configure(command=resume_selected_tasks)
    cancel_btn.configure(command=cancel_selected_tasks)

    def download_worker(task_id, video_url, out_dir, cookie, selected_option):
        task = state["tasks"][task_id]
        append_log("-" * 60)
        append_log(f"开始下载任务：{task['title']}")

        def progress_report(stage, percent=None, detail=""):
            status = stage
            if task["cancel_event"].is_set():
                status = "取消中"
            elif not task["pause_event"].is_set():
                status = "已暂停"
            update_task(task_id, status=status, percent=percent, detail=detail)

        ok = download_bilibili_video(
            video_url, cookie, output_dir=out_dir,
            logger=append_log, selected_option=selected_option,
            control={"pause_event": task["pause_event"], "cancel_event": task["cancel_event"]},
            progress_callback=progress_report,
        )

        def finish_ui():
            if task["cancel_event"].is_set():
                update_task(task_id, status="已取消", percent=task.get("percent", 0))
                append_log(f"任务已取消：{task['title']}", "warning")
            elif ok:
                update_task(task_id, status="完成", percent=100)
                append_log(f"任务完成：{task['title']}", "success")
            else:
                update_task(task_id, status="失败")
                append_log(f"任务失败：{task['title']}", "error")
        root.after(0, finish_ui)

    def start_download():
        video_url = url_var.get().strip()
        out_dir = folder_var.get().strip()
        if not video_url:
            messagebox.showwarning("提示", "请先填写 B站视频链接。")
            return
        if not out_dir:
            messagebox.showwarning("提示", "请先选择下载目录。")
            return
        if not state.get("cookie"):
            proceed = messagebox.askyesno(
                "未登录",
                "尚未获取 Cookie，将以未登录模式下载（可能仅低画质）。\n\n是否继续？"
            )
            if not proceed:
                return

        options = state.get("video_options", [])
        if options:
            selection = video_listbox.curselection()
            if not selection:
                messagebox.showwarning("提示", "请先在视频列表中选择一个或多个要下载的条目。")
                return
            selected_options = [options[i] for i in selection if i < len(options)]
        else:
            selected_options = [None]

        os.makedirs(out_dir, exist_ok=True)
        for selected_option in selected_options:
            task_id = uuid.uuid4().hex
            title = selected_option.get("title") if selected_option else f"当前链接 {len(state['task_order']) + 1}"
            pause_event = threading.Event()
            pause_event.set()
            state["tasks"][task_id] = {
                "seq": len(state["task_order"]) + 1,
                "title": title,
                "status": "等待中",
                "percent": 0.0,
                "pause_event": pause_event,
                "cancel_event": threading.Event(),
            }
            refresh_task_row(task_id)
            threading.Thread(
                target=download_worker,
                args=(task_id, video_url, out_dir, state["cookie"], selected_option),
                daemon=True
            ).start()

        append_log(f"已启动 {len(selected_options)} 个下载任务。", "success")

    download_btn.configure(command=start_download)

    append_log("界面已就绪。合集或分P视频请先点击「解析列表」，按住 Ctrl 可多选条目并行下载。")
    append_log("提示：若 Dolby Vision/HDR 视频在 PotPlayer 中发灰，请切换渲染器为「内置 Direct3D 11 渲染器」。")

    root.mainloop()


if __name__ == "__main__":
    launch_gui()
