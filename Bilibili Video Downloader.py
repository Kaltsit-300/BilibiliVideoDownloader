import requests
import json
import re
import os
import base64
import sqlite3
import shutil
import tempfile
import ctypes
from pathlib import Path
import time
import sys
import subprocess
from urllib.parse import urlparse, parse_qs, unquote, quote_plus
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import io
import contextlib
import webbrowser
try:
    from moviepy.video.io import ffmpeg_tools
except ImportError:
    ffmpeg_tools = None
try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None
try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None

REQUIRED_COOKIE_KEYS = {"SESSDATA", "bili_jct", "DedeUserID"}
ELEVATE_FLAG = "--bvd-elevated"


def _is_windows_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin():
    try:
        if _is_windows_admin():
            print("ℹ️ 当前已经是管理员权限，无需重复提权。")
            return False
        script_path = os.path.abspath(sys.argv[0])
        args = [script_path] + [a for a in sys.argv[1:] if a != ELEVATE_FLAG]
        args.append(ELEVATE_FLAG)
        params = subprocess.list2cmdline(args)
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        if ret <= 32:
            print(f"❌ 请求管理员权限失败，返回码: {ret}")
            return False
        print("✅ 已发起管理员权限请求，请在弹出的 UAC 窗口中点击“是”。")
        return True
    except Exception as e:
        print(f"❌ 提权失败: {e}")
        return False


def _ensure_browser_cookie3():
    global browser_cookie3
    if browser_cookie3 is not None:
        return True
    print("   - 检测到 browser_cookie3 未安装，正在尝试自动安装...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "browser-cookie3"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        import browser_cookie3 as bc3
        browser_cookie3 = bc3
        print("   - browser_cookie3 安装成功。")
        return True
    except Exception as e:
        print(f"   - 自动安装 browser_cookie3 失败: {e}")
        return False


def _dpapi_decrypt(encrypted_bytes):
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', ctypes.c_uint), ('pbData', ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB(len(encrypted_bytes), ctypes.create_string_buffer(encrypted_bytes))
    out_blob = DATA_BLOB()

    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()

    try:
        pointer = ctypes.cast(out_blob.pbData, ctypes.POINTER(ctypes.c_char))
        return ctypes.string_at(pointer, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _get_chromium_master_key(local_state_path):
    with open(local_state_path, 'r', encoding='utf-8') as f:
        local_state = json.load(f)
    encrypted_key = base64.b64decode(local_state['os_crypt']['encrypted_key'])
    if encrypted_key.startswith(b'DPAPI'):
        encrypted_key = encrypted_key[5:]
    return _dpapi_decrypt(encrypted_key)


def _decrypt_chromium_cookie(encrypted_value, master_key):
    if not encrypted_value:
        return ""

    # Chrome/Edge 新版 cookie 通常为 v10/v11 + AES-GCM
    if encrypted_value.startswith((b'v10', b'v11')):
        if AES is None:
            raise RuntimeError("缺少 pycryptodome，无法解密新版浏览器 Cookie。请先安装: pip install pycryptodome")
        nonce = encrypted_value[3:15]
        cipher_text = encrypted_value[15:-16]
        tag = encrypted_value[-16:]
        cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(cipher_text, tag).decode('utf-8', errors='ignore')

    # 新版 App-Bound Encryption（v20）外部进程通常无法直接解密
    if encrypted_value.startswith(b'v20'):
        raise RuntimeError("检测到 v20 加密，当前方案无法直接解密该条 Cookie。")

    # 老版 Chromium 可能直接走 DPAPI
    return _dpapi_decrypt(encrypted_value).decode('utf-8', errors='ignore')


def _cookie_dict_to_header(cookie_dict):
    filtered = {}
    for k, v in cookie_dict.items():
        if v:
            filtered[k] = v
    return "; ".join([f"{k}={v}" for k, v in filtered.items()])


def _is_cookie_usable(cookie_header):
    if not cookie_header:
        return False
    hits = 0
    for key in REQUIRED_COOKIE_KEYS:
        if f"{key}=" in cookie_header:
            hits += 1
    return hits >= 1


def _cookiejar_to_header(cookiejar):
    cookie_dict = {}
    for c in cookiejar:
        if "bilibili.com" in c.domain:
            cookie_dict[c.name] = c.value
    return _cookie_dict_to_header(cookie_dict)


def _try_browser_cookie3():
    logs = []
    if browser_cookie3 is None and not _ensure_browser_cookie3():
        logs.append("browser_cookie3 不存在，跳过通用浏览器接口。")
        return "", logs

    getters = [
        ("Chrome", browser_cookie3.chrome),
        ("Edge", browser_cookie3.edge),
        ("Brave", browser_cookie3.brave),
        ("Firefox", browser_cookie3.firefox),
        ("Opera", browser_cookie3.opera),
    ]
    for name, getter in getters:
        try:
            try:
                jar = getter(domain_name=".bilibili.com")
            except TypeError:
                jar = getter()
            cookie = _cookiejar_to_header(jar)
            if _is_cookie_usable(cookie):
                logs.append(f"{name}: 读取成功。")
                return cookie, logs
            logs.append(f"{name}: 未发现可用 B站 Cookie。")
        except Exception as e:
            logs.append(f"{name}: 失败 -> {str(e)}")
    return "", logs


def _parse_cookie_from_callback_url(callback_url):
    parsed = urlparse(callback_url)
    query = parse_qs(parsed.query)
    key_order = [
        "SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid", "buvid3", "buvid4"
    ]
    cookie_dict = {}
    for key in key_order:
        values = query.get(key, [])
        if values:
            cookie_dict[key] = unquote(values[0])
    return _cookie_dict_to_header(cookie_dict)


def _get_cookie_by_qr_login():
    print("   - 尝试兜底方案：B站二维码登录获取 Cookie ...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    try:
        gen_api = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
        gen_data = requests.get(gen_api, headers=headers, timeout=15).json()
        if gen_data.get("code") != 0:
            print(f"   - 二维码生成失败: {gen_data}")
            return ""
        qrcode_key = gen_data["data"]["qrcode_key"]
        qrcode_url = gen_data["data"]["url"]
        desktop_qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=320x320&data={quote_plus(qrcode_url)}"
        print("\n请在电脑浏览器打开下面二维码图片链接，然后用手机 B站 App 扫码：")
        print(desktop_qr_url)

        poll_api = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
        for _ in range(90):
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
            if status_code == 0:
                callback_url = inner.get("url", "")
                cookie = _parse_cookie_from_callback_url(callback_url)
                if _is_cookie_usable(cookie):
                    print("✅ 扫码登录成功，已获取可用 Cookie。")
                    return cookie
                print("   - 扫码成功，但回调中未拿到完整 Cookie 字段。")
                return ""
            if status_code == 86038:
                print("   - 二维码已失效，请重试。")
                return ""
        print("   - 等待扫码超时。")
        return ""
    except Exception as e:
        print(f"   - 二维码兜底方案失败: {e}")
        return ""


def _extract_bilibili_cookie_from_chromium(user_data_dir, browser_name):
    logs = []
    local_state_path = os.path.join(user_data_dir, "Local State")
    if not os.path.exists(local_state_path):
        logs.append(f"{browser_name}: 未找到 Local State。")
        return "", logs

    try:
        master_key = _get_chromium_master_key(local_state_path)
    except Exception as e:
        logs.append(f"{browser_name}: 读取主密钥失败 -> {str(e)}")
        return "", logs

    profile_dirs = ["Default"]
    if os.path.exists(user_data_dir):
        for name in os.listdir(user_data_dir):
            if name.startswith("Profile "):
                profile_dirs.append(name)

    for profile in profile_dirs:
        cookie_db_candidates = [
            os.path.join(user_data_dir, profile, "Network", "Cookies"),
            os.path.join(user_data_dir, profile, "Cookies"),
        ]
        for cookie_db in cookie_db_candidates:
            if not os.path.exists(cookie_db):
                continue

            tmp_db = os.path.join(tempfile.gettempdir(), f"bili_cookie_{browser_name}_{profile}.db")
            try:
                rows = []
                try:
                    # 首选复制到临时文件，避免数据库锁影响
                    shutil.copy2(cookie_db, tmp_db)
                    conn = sqlite3.connect(tmp_db)
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT name, value, encrypted_value FROM cookies WHERE host_key LIKE ?",
                        ("%bilibili.com%",)
                    )
                    rows = cursor.fetchall()
                    conn.close()
                except Exception:
                    # 若复制失败（例如文件占用），尝试只读直连原库
                    db_uri = Path(cookie_db).as_uri() + "?mode=ro"
                    conn = sqlite3.connect(db_uri, uri=True)
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT name, value, encrypted_value FROM cookies WHERE host_key LIKE ?",
                        ("%bilibili.com%",)
                    )
                    rows = cursor.fetchall()
                    conn.close()

                if not rows:
                    continue

                cookie_dict = {}
                decrypt_failed = 0
                v20_count = 0
                for name, value, encrypted_value in rows:
                    if value:
                        cookie_dict[name] = value
                        continue
                    try:
                        cookie_dict[name] = _decrypt_chromium_cookie(encrypted_value, master_key)
                    except Exception as e:
                        if "v20" in str(e):
                            v20_count += 1
                        decrypt_failed += 1

                cookie_header = _cookie_dict_to_header(cookie_dict)
                if _is_cookie_usable(cookie_header):
                    logs.append(f"{browser_name}-{profile}: 读取成功。")
                    return cookie_header, logs

                logs.append(
                    f"{browser_name}-{profile}: 发现 {len(rows)} 条记录，但可用字段不足"
                    f"（解密失败 {decrypt_failed} 条，v20 {v20_count} 条）。"
                )
            except Exception as e:
                logs.append(f"{browser_name}-{profile}: 读取失败 -> {str(e)}")
            finally:
                if os.path.exists(tmp_db):
                    os.remove(tmp_db)
    return "", logs


def auto_get_bilibili_cookie():
    print("\n🍪 正在尝试一键读取浏览器中的 B 站 Cookie ...")
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")

    # 第一层：browser_cookie3 通用方案（通常成功率较高）
    cookie, logs = _try_browser_cookie3()
    need_admin_hint = False
    lock_or_permission_hint = False
    for line in logs:
        print(f"   - {line}")
        if "requires admin" in line.lower():
            need_admin_hint = True
        if "permission" in line.lower() or "denied" in line.lower():
            lock_or_permission_hint = True
    if _is_cookie_usable(cookie):
        print("✅ 已通过通用浏览器接口获取到可用 Cookie。")
        return cookie

    # 第二层：本地数据库兜底
    browser_candidates = [
        ("Edge", os.path.join(local, "Microsoft", "Edge", "User Data")),
        ("Chrome", os.path.join(local, "Google", "Chrome", "User Data")),
        ("Brave", os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")),
        ("360Chrome", os.path.join(local, "360Chrome", "Chrome", "User Data")),
        ("QQBrowser", os.path.join(local, "Tencent", "QQBrowser", "User Data")),
        ("Sogou", os.path.join(roaming, "SogouExplorer", "Webkit", "User Data")),
    ]

    for browser_name, user_data_dir in browser_candidates:
        cookie, detail_logs = _extract_bilibili_cookie_from_chromium(user_data_dir, browser_name)
        for line in detail_logs:
            print(f"   - {line}")
            lower_line = line.lower()
            if "unable to open database file" in lower_line or "winerror 32" in lower_line:
                lock_or_permission_hint = True
        if _is_cookie_usable(cookie):
            hit_keys = [k for k in REQUIRED_COOKIE_KEYS if f"{k}=" in cookie]
            print(f"✅ 已从 {browser_name} 读取到可用 Cookie（命中字段: {', '.join(hit_keys)}）。")
            return cookie

    # 第三层：扫码登录兜底（不依赖本地浏览器数据库权限）
    cookie = _get_cookie_by_qr_login()
    if _is_cookie_usable(cookie):
        return cookie

    print("⚠️ 仍未自动读取到可用 Cookie，将使用未登录模式下载（可能仅低画质）。")
    if need_admin_hint and not _is_windows_admin():
        print("ℹ️ 检测到浏览器 Cookie 读取需要管理员权限，请尝试“以管理员身份运行”本脚本后再按 c 重试。")
    if lock_or_permission_hint and not _is_windows_admin():
        print("ℹ️ 检测到 Cookie 数据库被占用或权限不足，管理员模式下成功率更高。")
    if AES is None:
        print("ℹ️ 缺少解密依赖，请安装: pip install pycryptodome")
    if browser_cookie3 is None:
        print("ℹ️ 建议安装通用读取库: pip install browser-cookie3")
    print("ℹ️ 建议保持浏览器登录 B站后重试，优先使用 Edge/Chrome。")
    return ""


def download_bilibili_video(url, cookie_str="", output_dir=None, logger=None):
    # 可自定义下载路径，默认为桌面路径
    desktop_path = output_dir or os.path.join(os.path.expanduser("~"), "Desktop")

    def emit(msg):
        if logger:
            logger(msg)
        else:
            print(msg)

    # 为确保使用最高画质下载，请填入你自己的cookie
    # 如果不填入自己的cookie，仍然可以下载视频，哔哩哔哩官方会认为你没有登录，只能下载低画质视频
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36',
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

    # 1. 抓取并清理标题
    title_match = re.search(r'<title.*?>(.*?)</title>', html_data)
    if title_match:
        raw_title = title_match.group(1).replace('_哔哩哔哩_bilibili', '').strip()
        title = re.sub(r'[\\/:*?"<>|]', '_', raw_title)
    else:
        title = "未命名视频"
    emit(f"成功抓取视频标题：【{title}】")

    # 2. 提取 bvid/cid，并调用官方 playurl 接口请求最高可用画质
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

    playurl_api = "https://api.bilibili.com/x/player/playurl"
    play_params = {
        "bvid": bvid,
        "cid": cid,
        "qn": 127,       # 尽量请求最高画质
        "fnval": 4048,   # 支持 DASH / HDR / 4K / 8K 等组合
        "fourk": 1
    }
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

        # 将视频流按编码格式分类 (B站规则: 12=HEVC, 13=AV1, 7=AVC)
        hevc_list = [v for v in video_list if v.get('codecid') == 12]
        av1_list = [v for v in video_list if v.get('codecid') == 13]
        avc_list = [v for v in video_list if v.get('codecid') == 7]

        # 优先级排序：HEVC > AV1 > AVC(容易发灰)
        if hevc_list:
            target_video_list = hevc_list
            codec_name = "HEVC (H.265)"
        elif av1_list:
            target_video_list = av1_list
            codec_name = "AV1"
        elif avc_list:
            target_video_list = avc_list
            codec_name = "AVC (H.264)"
        else:
            target_video_list = video_list
            codec_name = "未知编码"

        # 在选定的编码流中，挑选最高画质（根据 id 和 码率带宽 排序）
        best_video = sorted(target_video_list, key=lambda x: (x['id'], x['bandwidth']), reverse=True)[0]
        video_url = best_video.get('baseUrl') or best_video.get('base_url')
        video_id = best_video['id']

        # 挑选最高音质
        best_audio = sorted(audio_list, key=lambda x: x['id'], reverse=True)[0]
        audio_url = best_audio.get('baseUrl') or best_audio.get('base_url')
        if not video_url or not audio_url:
            emit("解析失败：未拿到可用的音视频下载地址。")
            return False

        # 打印画质与编码信息
        quality_map = {127: "8K", 120: "4K", 116: "1080P 60帧", 112: "1080P 高码率", 80: "1080P", 74: "720P 60帧",
                       64: "720P", 32: "480P", 16: "360P"}
        quality_str = quality_map.get(video_id, f"未知画质(ID:{video_id})")
        emit(f"嗅探成功：画质【{quality_str}】 | 编码【{codec_name}】")

        if video_id <= 32:
            emit("警告：当前画质仅为 480P 或更低，可能是 Cookie 权限不足或已过期。")

    except KeyError:
        emit("解析失败，数据结构可能发生变化。")
        return False

    # 4. 文件路径准备
    if not os.path.exists(desktop_path):
        os.makedirs(desktop_path)

    video_temp = "temp_video.mp4"
    audio_temp = "temp_audio.mp4"
    output_filename = os.path.join(desktop_path, f"{title}.mp4")

    # 5. 下载函数
    def download_file(download_url, filename, desc):
        emit(f"正在下载 {desc} ...")
        with requests.get(download_url, headers=headers, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        emit(f"{desc} 下载完成。")

    # 执行下载
    download_file(video_url, video_temp, "【视频画面】")
    download_file(audio_url, audio_temp, "【音频声音】")

    # 6. 音视频混流合成
    emit(f"\n正在合成，输出文件：\n{output_filename}")
    try:
        if ffmpeg_tools is None:
            emit("未安装 moviepy，无法合成音视频。请先安装: pip install moviepy")
            return False
        if os.path.exists(output_filename):
            os.remove(output_filename)  # 覆盖已存在的文件

        ffmpeg_tools.ffmpeg_merge_video_audio(video_temp, audio_temp, output_filename)
        emit("下载与合成完成。")
        emit("提示：若默认播放器无法播放画面，请尝试 PotPlayer 或 VLC。")
        return True
    except Exception as e:
        emit(f"合成失败: {e}")
        return False
    finally:
        if os.path.exists(video_temp): os.remove(video_temp)
        if os.path.exists(audio_temp): os.remove(audio_temp)

def launch_gui():
    root = tk.Tk()
    root.title("B站视频下载器")
    root.geometry("980x680")
    root.minsize(900, 620)
    root.configure(bg="#0f172a")

    state = {"cookie": ""}
    default_dir = os.path.join(os.path.expanduser("~"), "Desktop")
    url_pattern = re.compile(r"https?://[^\s]+")
    link_counter = {"n": 0}
    font_family = "Microsoft YaHei UI"
    colors = {
        "bg": "#0f172a",
        "card": "#111827",
        "card_soft": "#1f2937",
        "text": "#e5e7eb",
        "muted": "#94a3b8",
        "accent": "#f97316",
        "accent_hover": "#ea580c",
        "ok": "#22c55e",
        "warn": "#f59e0b",
        "input_bg": "#0b1220",
        "input_fg": "#f8fafc",
    }

    cookie_status_var = tk.StringVar(value="Cookie 状态：未获取")
    folder_var = tk.StringVar(value=default_dir)
    url_var = tk.StringVar(value="")

    shell = tk.Frame(root, bg=colors["bg"], padx=20, pady=20)
    shell.pack(fill="both", expand=True)

    header = tk.Frame(shell, bg=colors["bg"])
    header.pack(fill="x", pady=(0, 14))
    tk.Label(
        header,
        text="Bilibili Video Downloader",
        bg=colors["bg"],
        fg=colors["text"],
        font=(font_family, 19, "bold")
    ).pack(anchor="w")
    tk.Label(
        header,
        text="高质量下载 / Cookie 自动获取 / 一键保存",
        bg=colors["bg"],
        fg=colors["muted"],
        font=(font_family, 10)
    ).pack(anchor="w", pady=(2, 0))

    form_card = tk.Frame(shell, bg=colors["card"], padx=16, pady=16, highlightthickness=1, highlightbackground="#243047")
    form_card.pack(fill="x")

    tk.Label(form_card, text="视频链接", bg=colors["card"], fg=colors["text"], font=(font_family, 11, "bold")).grid(row=0, column=0, sticky="w")
    url_entry = tk.Entry(
        form_card, textvariable=url_var, width=80,
        bg=colors["input_bg"], fg=colors["input_fg"], insertbackground=colors["input_fg"],
        relief="flat", font=(font_family, 11)
    )
    url_entry.grid(row=1, column=0, columnspan=4, sticky="we", pady=(6, 12), ipady=7)

    tk.Label(form_card, text="下载目录", bg=colors["card"], fg=colors["text"], font=(font_family, 11, "bold")).grid(row=2, column=0, sticky="w")
    folder_entry = tk.Entry(
        form_card, textvariable=folder_var, width=70,
        bg=colors["input_bg"], fg=colors["input_fg"], insertbackground=colors["input_fg"],
        relief="flat", font=(font_family, 10)
    )
    folder_entry.grid(row=3, column=0, columnspan=2, sticky="we", pady=(6, 0), ipady=7)

    def choose_folder():
        selected = filedialog.askdirectory(initialdir=folder_var.get() or default_dir)
        if selected:
            folder_var.set(selected)

    def style_button(btn, is_primary=False):
        normal_bg = colors["accent"] if is_primary else colors["card_soft"]
        hover_bg = colors["accent_hover"] if is_primary else "#374151"
        btn.configure(
            bg=normal_bg, fg="#ffffff", relief="flat",
            activebackground=hover_bg, activeforeground="#ffffff",
            cursor="hand2", font=(font_family, 10, "bold"), padx=14, pady=8, bd=0
        )
        btn.bind("<Enter>", lambda _e: btn.configure(bg=hover_bg))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=normal_bg))

    def append_log(msg):
        def _write():
            log_text.configure(state="normal")

            text = str(msg)
            pos = 0
            for match in url_pattern.finditer(text):
                if match.start() > pos:
                    log_text.insert("end", text[pos:match.start()])
                url = match.group(0)
                tag_name = f"link_{link_counter['n']}"
                link_counter["n"] += 1
                log_text.insert("end", url, ("hyperlink", tag_name))
                log_text.tag_bind(tag_name, "<Button-1>", lambda _e, u=url: webbrowser.open(u))
                pos = match.end()
            if pos < len(text):
                log_text.insert("end", text[pos:])
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

    def get_cookie_worker():
        append_log("开始获取 B站 Cookie ...")
        redirector = _LogRedirect(append_log)
        with contextlib.redirect_stdout(redirector), contextlib.redirect_stderr(redirector):
            cookie = auto_get_bilibili_cookie()
            redirector.flush()
        if cookie:
            state["cookie"] = cookie
            hit_keys = [k for k in REQUIRED_COOKIE_KEYS if f"{k}=" in cookie]
            root.after(0, lambda: cookie_status_var.set(f"Cookie 状态：已获取（命中 {', '.join(hit_keys)}）"))
            append_log("Cookie 获取成功。")
        else:
            root.after(0, lambda: cookie_status_var.set("Cookie 状态：获取失败"))
            append_log("Cookie 获取失败。")
            append_log("提示：可尝试使用“管理员模式”启动本程序后，再次点击“获取 Cookie”。")
            if not _is_windows_admin():
                def ask_and_maybe_restart():
                    should_elevate = messagebox.askyesno("权限建议", "检测到可能需要管理员权限，是否立即以管理员身份重启程序？")
                    if should_elevate and _relaunch_as_admin():
                        root.after(200, root.destroy)
                root.after(0, ask_and_maybe_restart)
        root.after(0, lambda: cookie_btn.config(state="normal"))

    def get_cookie_action():
        cookie_btn.config(state="disabled")
        threading.Thread(target=get_cookie_worker, daemon=True).start()

    folder_btn = tk.Button(form_card, text="选择文件夹", command=choose_folder, width=12)
    folder_btn.grid(row=3, column=2, padx=(10, 8), sticky="we")
    style_button(folder_btn, is_primary=False)

    cookie_btn = tk.Button(form_card, text="获取 Cookie", command=get_cookie_action, width=12)
    cookie_btn.grid(row=3, column=3, sticky="we")
    style_button(cookie_btn, is_primary=False)

    form_card.grid_columnconfigure(0, weight=1)
    form_card.grid_columnconfigure(1, weight=2)

    status_frame = tk.Frame(shell, bg=colors["bg"], pady=10)
    status_frame.pack(fill="x")
    tk.Label(
        status_frame,
        textvariable=cookie_status_var,
        anchor="w",
        bg=colors["bg"],
        fg=colors["ok"],
        font=(font_family, 10, "bold")
    ).pack(fill="x")

    action_frame = tk.Frame(shell, bg=colors["bg"], pady=4)
    action_frame.pack(fill="x")

    def download_worker(video_url, out_dir, cookie):
        append_log("-" * 60)
        append_log("开始下载任务...")
        ok = download_bilibili_video(video_url, cookie, output_dir=out_dir, logger=append_log)
        def finish_ui():
            if ok:
                append_log("任务完成。")
                messagebox.showinfo("完成", "视频下载完成。")
            else:
                append_log("任务失败，请查看日志。")
                messagebox.showerror("失败", "下载失败，请查看下方日志。")
            download_btn.config(state="normal")
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
        os.makedirs(out_dir, exist_ok=True)
        download_btn.config(state="disabled")
        threading.Thread(target=download_worker, args=(video_url, out_dir, state["cookie"]), daemon=True).start()

    download_btn = tk.Button(action_frame, text="开始下载", command=start_download, width=16)
    style_button(download_btn, is_primary=True)
    download_btn.pack(anchor="w")

    log_frame = tk.Frame(shell, bg=colors["card"], padx=14, pady=12, highlightthickness=1, highlightbackground="#243047")
    log_frame.pack(fill="both", expand=True)
    tk.Label(log_frame, text="运行日志", bg=colors["card"], fg=colors["text"], font=(font_family, 11, "bold")).pack(anchor="w")
    log_text = ScrolledText(
        log_frame, height=18, wrap="word", state="disabled",
        bg="#090f1a", fg="#d1d5db", insertbackground="#d1d5db", relief="flat",
        font=("Consolas", 10)
    )
    log_text.pack(fill="both", expand=True, pady=(6, 0))
    log_text.tag_configure("hyperlink", foreground="#60a5fa", underline=True)
    log_text.tag_bind("hyperlink", "<Enter>", lambda _e: log_text.config(cursor="hand2"))
    log_text.tag_bind("hyperlink", "<Leave>", lambda _e: log_text.config(cursor="arrow"))

    append_log("界面已就绪。建议先点击“获取 Cookie”，再开始下载。")
    append_log("若 Cookie 获取失败，可使用管理员模式启动本程序后重试。")
    if ELEVATE_FLAG in sys.argv:
        append_log("当前已在管理员模式启动。")

    root.mainloop()


if __name__ == "__main__":
    launch_gui()
