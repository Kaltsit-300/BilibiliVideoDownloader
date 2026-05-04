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

def download_bilibili_video(url, cookie_str="", output_dir=None, logger=None):
    desktop_path = output_dir or os.path.join(os.path.expanduser("~"), "Desktop")

    def emit(msg):
        if logger:
            logger(msg)
        else:
            print(msg)

    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/89.0.4389.90 Safari/537.36'
        ),
        'Referer': url,
        'cookie': cookie_str
    }

    if not re.match(r"^https?://", url) or "bilibili.com/video/" not in url:
        emit("链接格式不正确，请粘贴完整的 B站视频链接（例如 https://www.bilibili.com/video/BV...）。")
        return False

    emit("\n正在解析网页，提取视频数据...")
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.encoding = 'utf-8'
        html_data = response.text
    except Exception as e:
        emit(f"网络请求失败: {e}")
        return False

    # 提取标题
    title_match = re.search(r'<title.*?>(.*?)</title>', html_data)
    if title_match:
        raw_title = title_match.group(1).replace('_哔哩哔哩_bilibili', '').strip()
        title = re.sub(r'[\\/:*?"<>|]', '_', raw_title)
    else:
        title = "未命名视频"
    emit(f"成功抓取视频标题：【{title}】")

    # 提取 bvid/cid
    bvid_match = re.search(r"/video/(BV[0-9A-Za-z]+)", url)
    if not bvid_match:
        bvid_match = re.search(r'"bvid":"(BV[0-9A-Za-z]+)"', html_data)
    if not bvid_match:
        emit("解析失败，无法识别 BVID。请确认链接是普通视频页。")
        return False
    bvid = bvid_match.group(1)

    cid = None
    state_match = re.search(r'window\.__INITIAL_STATE__=(.*?);\(function', html_data)
    if state_match:
        try:
            initial_state = json.loads(state_match.group(1))
            video_data = initial_state.get("videoData", {})
            cid = video_data.get("cid")
            if not cid:
                pages = video_data.get("pages", [])
                if pages:
                    cid = pages[0].get("cid")
        except Exception:
            cid = None
    if not cid:
        cid_match = re.search(r'"cid":(\d+)', html_data)
        if cid_match:
            cid = int(cid_match.group(1))
    if not cid:
        emit("解析失败，无法识别 CID。")
        return False

    # 调用播放接口
    playurl_api = "https://api.bilibili.com/x/player/playurl"
    play_params = {"bvid": bvid, "cid": cid, "qn": 127, "fnval": 4048, "fourk": 1}
    try:
        play_resp = requests.get(playurl_api, params=play_params, headers=headers, timeout=20)
        play_json = play_resp.json()
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

        # 编码优先级：HEVC > AV1 > AVC
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

    # 文件路径与下载
    if not os.path.exists(desktop_path):
        os.makedirs(desktop_path)

    video_temp = "temp_video.mp4"
    audio_temp = "temp_audio.mp4"
    output_filename = os.path.join(desktop_path, f"{title}.mp4")

    def download_file(download_url, filename, desc):
        emit(f"正在下载 {desc} ...")
        with requests.get(download_url, headers=headers, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        emit(f"{desc} 下载完成。")

    download_file(video_url, video_temp, "【视频画面】")
    download_file(audio_url, audio_temp, "【音频声音】")

    # 合成
    emit(f"\n正在合成，输出文件：\n{output_filename}")
    try:
        if ffmpeg_tools is None:
            emit("未安装 moviepy，无法合成音视频。请先安装: pip install moviepy")
            return False
        if os.path.exists(output_filename):
            os.remove(output_filename)

        ffmpeg_tools.ffmpeg_merge_video_audio(video_temp, audio_temp, output_filename)
        emit("下载与合成完成。")
        emit("提示：若默认播放器无法播放画面，请尝试 PotPlayer 或 VLC。")
        return True
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
    root.geometry("980x700")
    root.minsize(900, 640)

    state = {"cookie": ""}
    default_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    url_pattern = re.compile(r"https?://[^\s]+")
    link_counter = {"n": 0}
    font_family = "Microsoft YaHei UI"
    mono_font = ("Consolas", 10)

    colors = {
        "bg": "#0a0f1a",
        "surface": "#111827",
        "card": "#1e293b",
        "border": "#334155",
        "text": "#f1f5f9",
        "muted": "#94a3b8",
        "dim": "#64748b",
        "accent": "#3b82f6",
        "accent_hover": "#2563eb",
        "accent_soft": "#1d4ed8",
        "success": "#22c55e",
        "warning": "#f59e0b",
        "danger": "#ef4444",
        "input_bg": "#0f172a",
        "input_fg": "#e2e8f0",
        "log_bg": "#050a13",
        "log_text": "#cbd5e1",
    }

    cookie_status_var = tk.StringVar(value="\U0001f511 Cookie 未获取")
    folder_var = tk.StringVar(value=default_dir)
    url_var = tk.StringVar(value="")

    # ---- 容器 ----
    shell = tk.Frame(root, bg=colors["bg"], padx=24, pady=20)
    shell.pack(fill="both", expand=True)

    # ---- 标题区 ----
    header = tk.Frame(shell, bg=colors["bg"])
    header.pack(fill="x", pady=(0, 16))

    title_frame = tk.Frame(header, bg=colors["bg"])
    title_frame.pack(anchor="w")
    tk.Label(
        title_frame, text="Bilibili ", bg=colors["bg"], fg=colors["accent"],
        font=(font_family, 22, "bold"), anchor="w"
    ).pack(side="left")
    tk.Label(
        title_frame, text="Video Downloader", bg=colors["bg"], fg=colors["text"],
        font=(font_family, 22, "bold"), anchor="w"
    ).pack(side="left")

    tk.Label(
        header, text="高质量下载  ·  扫码登录  ·  自动合并音视频",
        bg=colors["bg"], fg=colors["dim"],
        font=(font_family, 10), anchor="w"
    ).pack(anchor="w", pady=(6, 0))

    # ---- 分隔线 ----
    sep = tk.Frame(shell, bg=colors["border"], height=1)
    sep.pack(fill="x", pady=(0, 16))

    # ---- 输入卡片 ----
    input_card = tk.Frame(shell, bg=colors["card"], padx=20, pady=18)
    input_card.pack(fill="x")

    # URL 输入
    url_row = tk.Frame(input_card, bg=colors["card"])
    url_row.pack(fill="x", pady=(0, 12))
    tk.Label(
        url_row, text="\U0001f517 视频链接", bg=colors["card"], fg=colors["text"],
        font=(font_family, 11, "bold"), anchor="w"
    ).pack(side="left")
    url_entry = tk.Entry(
        url_row, textvariable=url_var,
        bg=colors["input_bg"], fg=colors["input_fg"],
        insertbackground=colors["input_fg"],
        relief="flat", font=(font_family, 11),
        highlightthickness=1, highlightcolor=colors["border"],
        highlightbackground=colors["border"]
    )
    url_entry.pack(side="left", fill="x", expand=True, padx=(12, 0), ipady=6)

    def _on_focus_in(e):
        e.widget.configure(highlightcolor=colors["accent"])
    def _on_focus_out(e):
        e.widget.configure(highlightcolor=colors["border"])
    url_entry.bind("<FocusIn>", _on_focus_in)
    url_entry.bind("<FocusOut>", _on_focus_out)

    # 设置行：目录选择 + 按钮
    settings_row = tk.Frame(input_card, bg=colors["card"])
    settings_row.pack(fill="x")

    tk.Label(
        settings_row, text="\U0001f4c1 下载目录", bg=colors["card"], fg=colors["text"],
        font=(font_family, 11, "bold"), anchor="w"
    ).pack(side="left")

    folder_entry = tk.Entry(
        settings_row, textvariable=folder_var,
        bg=colors["input_bg"], fg=colors["input_fg"],
        insertbackground=colors["input_fg"],
        relief="flat", font=(font_family, 10),
        highlightthickness=1, highlightcolor=colors["border"],
        highlightbackground=colors["border"]
    )
    folder_entry.pack(side="left", fill="x", expand=True, padx=(12, 10), ipady=5)
    folder_entry.bind("<FocusIn>", _on_focus_in)
    folder_entry.bind("<FocusOut>", _on_focus_out)

    def choose_folder():
        selected = filedialog.askdirectory(initialdir=folder_var.get() or default_dir)
        if selected:
            folder_var.set(selected)

    def create_modern_button(parent, text, command, is_primary=False, is_danger=False, width=None):
        if is_primary:
            normal_bg, hover_bg = colors["accent"], colors["accent_hover"]
        elif is_danger:
            normal_bg, hover_bg = "#7f1d1d", "#991b1b"
        else:
            normal_bg, hover_bg = colors["surface"], colors["border"]

        btn = tk.Button(
            parent, text=text, command=command,
            bg=normal_bg, fg=colors["text"], relief="flat",
            activebackground=hover_bg, activeforeground="#ffffff",
            cursor="hand2", font=(font_family, 10, "bold"),
            padx=16, pady=7, bd=0, width=width
        )
        btn.bind("<Enter>", lambda _e, b=btn, h=hover_bg: b.configure(bg=h))
        btn.bind("<Leave>", lambda _e, b=btn, n=normal_bg: b.configure(bg=n))
        return btn

    folder_btn = create_modern_button(settings_row, "\U0001f4c2 选择文件夹", choose_folder, width=14)
    folder_btn.pack(side="right", padx=(4, 0))

    cookie_btn = create_modern_button(settings_row, "\U0001f511 获取 Cookie", None, width=14)
    cookie_btn.pack(side="right")

    # ---- Cookie 状态指示 ----
    status_bar = tk.Frame(shell, bg=colors["bg"])
    status_bar.pack(fill="x", pady=(12, 0))

    status_dot = tk.Canvas(status_bar, width=10, height=10, bg=colors["bg"],
                           highlightthickness=0)
    status_dot.create_oval(1, 1, 9, 9, fill=colors["dim"], outline="")
    status_dot.pack(side="left", padx=(0, 8))

    status_label = tk.Label(
        status_bar, textvariable=cookie_status_var,
        bg=colors["bg"], fg=colors["dim"],
        font=(font_family, 10), anchor="w"
    )
    status_label.pack(side="left", fill="x", expand=True)

    def set_cookie_status(text, color):
        cookie_status_var.set(text)
        status_label.configure(fg=color)
        status_dot.delete("all")
        status_dot.create_oval(1, 1, 9, 9, fill=color, outline="")

    # ---- 操作按钮区 ----
    action_bar = tk.Frame(shell, bg=colors["bg"])
    action_bar.pack(fill="x", pady=(14, 0))

    download_btn = create_modern_button(
        action_bar, "\U0001f4be  开始下载", None, is_primary=True, width=18
    )
    download_btn.pack(side="left")

    clear_btn = create_modern_button(
        action_bar, "\U0001f5d1 清除缓存", None, is_danger=True, width=12
    )
    clear_btn.pack(side="left", padx=(10, 0))

    # ---- 日志区 ----
    log_card = tk.Frame(shell, bg=colors["card"], padx=16, pady=14)
    log_card.pack(fill="both", expand=True, pady=(16, 0))

    log_header = tk.Frame(log_card, bg=colors["card"])
    log_header.pack(fill="x", pady=(0, 8))
    tk.Label(
        log_header, text="\U0001f4cb 运行日志",
        bg=colors["card"], fg=colors["text"],
        font=(font_family, 11, "bold"), anchor="w"
    ).pack(side="left")

    log_text = ScrolledText(
        log_card, height=16, wrap="word", state="disabled",
        bg=colors["log_bg"], fg=colors["log_text"],
        insertbackground=colors["log_text"], relief="flat",
        font=mono_font, padx=10, pady=8
    )
    log_text.pack(fill="both", expand=True)
    log_text.tag_configure("hyperlink", foreground="#60a5fa", underline=True)
    log_text.tag_bind("hyperlink", "<Enter>", lambda _e: log_text.config(cursor="hand2"))
    log_text.tag_bind("hyperlink", "<Leave>", lambda _e: log_text.config(cursor="arrow"))
    log_text.tag_configure("success", foreground=colors["success"])
    log_text.tag_configure("warning", foreground=colors["warning"])
    log_text.tag_configure("error", foreground=colors["danger"])

    # ---- 日志追加函数 ----
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
                log_text.tag_bind(tag_name, "<Button-1>",
                                  lambda _e, u=url_link: webbrowser.open(u))
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

    # ---- Cookie 获取动作 ----
    def get_cookie_worker():
        append_log("\U0001f511 开始获取 B站 Cookie ...")
        redirector = _LogRedirect(append_log)
        with contextlib.redirect_stdout(redirector), contextlib.redirect_stderr(redirector):
            cookie = auto_get_bilibili_cookie(root_window=root)
            redirector.flush()
        if cookie:
            state["cookie"] = cookie
            hit_keys = [k for k in REQUIRED_COOKIE_KEYS if f"{k}=" in cookie]
            root.after(0, lambda: set_cookie_status(
                f"\U0001f511 Cookie 已获取（命中 {', '.join(hit_keys)}）",
                colors["success"]
            ))
            append_log("\u2705 Cookie 获取成功。", "success")
        else:
            root.after(0, lambda: set_cookie_status(
                "\U0001f512 Cookie 获取失败", colors["danger"]
            ))
            append_log("\u274c Cookie 获取失败。", "error")
            append_log("提示：点击「获取 Cookie」后弹出二维码，用 B站App 扫码即可。")
        root.after(0, lambda: cookie_btn.config(state="normal"))

    def get_cookie_action():
        cookie_btn.config(state="disabled")
        threading.Thread(target=get_cookie_worker, daemon=True).start()

    cookie_btn.configure(command=get_cookie_action)

    # ---- 清除缓存动作 ----
    def clear_cache_action():
        _clear_cookie_cache()
        state["cookie"] = ""
        set_cookie_status("\U0001f511 Cookie 已清除", colors["dim"])
        append_log("\U0001f5d1 Cookie 缓存已清除。下次下载需重新扫码获取。", "warning")

    clear_btn.configure(command=clear_cache_action)

    # ---- 下载动作 ----
    def download_worker(video_url, out_dir, cookie):
        append_log("-" * 60)
        append_log("\U0001f4e0 开始下载任务...")
        ok = download_bilibili_video(video_url, cookie, output_dir=out_dir, logger=append_log)
        def finish_ui():
            if ok:
                append_log("\u2705 任务完成。", "success")
                messagebox.showinfo("\u2705 完成", "视频下载完成！")
            else:
                append_log("\u274c 任务失败，请查看日志。", "error")
                messagebox.showerror("\u274c 失败", "下载失败，请查看下方日志。")
            download_btn.config(state="normal")
        root.after(0, finish_ui)

    def start_download():
        video_url = url_var.get().strip()
        out_dir = folder_var.get().strip()
        if not video_url:
            messagebox.showwarning("\u26a0\ufe0f 提示", "请先填写 B站视频链接。")
            return
        if not out_dir:
            messagebox.showwarning("\u26a0\ufe0f 提示", "请先选择下载目录。")
            return
        if not state.get("cookie"):
            proceed = messagebox.askyesno(
                "\u26a0\ufe0f 未登录",
                "尚未获取 Cookie，将以未登录模式下载（可能仅低画质）。\n\n是否继续？"
            )
            if not proceed:
                return
        os.makedirs(out_dir, exist_ok=True)
        download_btn.config(state="disabled")
        threading.Thread(
            target=download_worker,
            args=(video_url, out_dir, state["cookie"]),
            daemon=True
        ).start()

    download_btn.configure(command=start_download)

    # ---- 初始日志 ----
    append_log("\U0001f3af 界面已就绪。建议先点击「获取 Cookie」，再开始下载。")
    append_log("提示：若 Dolby Vision/HDR 视频在 PotPlayer 中发灰，")
    append_log("     请切换渲染器为「内置 Direct3D 11 渲染器」。")

    root.mainloop()


if __name__ == "__main__":
    launch_gui()
