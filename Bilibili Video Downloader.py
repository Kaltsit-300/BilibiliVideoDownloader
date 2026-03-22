import requests
import json
import re
import os
from moviepy.video.io import ffmpeg_tools
def download_bilibili_video(url):
    # 可自定义下载路径，默认为桌面路径
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")

    # 为确保使用最高画质下载，请填入你自己的cookie
    # 如果不填入自己的cookie，仍然可以下载视频，哔哩哔哩官方会认为你没有登录，只能下载低画质视频
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.90 Safari/537.36',
        'Referer': url,
        'cookie': ""
    }

    print("\n⏳ 正在解析网页，提取视频数据...")
    try:
        response = requests.get(url, headers=headers)
        response.encoding = 'utf-8'
        html_data = response.text
    except Exception as e:
        print(f"❌ 网络请求失败: {e}")
        return

    # 1. 抓取并清理标题
    title_match = re.search(r'<title.*?>(.*?)</title>', html_data)
    if title_match:
        raw_title = title_match.group(1).replace('_哔哩哔哩_bilibili', '').strip()
        title = re.sub(r'[\\/:*?"<>|]', '_', raw_title)
    else:
        title = "未命名视频"
    print(f"🎯 成功抓取视频标题：【{title}】")

    # 2. 提取并解析 JSON 数据
    playinfo_match = re.search(r'window\.__playinfo__=(.*?)</script>', html_data)
    if not playinfo_match:
        print("❌ 解析失败，找不到视频源流数据！请检查链接。")
        return

    playinfo_json = json.loads(playinfo_match.group(1))

    try:
        video_list = playinfo_json['data']['dash']['video']

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
        video_url = best_video['baseUrl']
        video_id = best_video['id']

        # 挑选最高音质
        audio_list = playinfo_json['data']['dash']['audio']
        best_audio = sorted(audio_list, key=lambda x: x['id'], reverse=True)[0]
        audio_url = best_audio['baseUrl']

        # 打印画质与编码信息
        quality_map = {127: "8K", 120: "4K", 116: "1080P 60帧", 112: "1080P 高码率", 80: "1080P", 74: "720P 60帧",
                       64: "720P", 32: "480P", 16: "360P"}
        quality_str = quality_map.get(video_id, f"未知画质(ID:{video_id})")
        print(f"📺 嗅探成功！当前画质:【{quality_str}】 | 编码格式:【{codec_name}】 ")

        if video_id <= 32:
            print("⚠️ 警告：当前画质仅为 480P 或更低！极大概率是您的 Cookie 权限不足或已过期。")

    except KeyError:
        print("❌ 解析失败，数据结构可能发生了变化！")
        return

    # 4. 文件路径准备
    if not os.path.exists(desktop_path):
        os.makedirs(desktop_path)

    video_temp = "temp_video.mp4"
    audio_temp = "temp_audio.mp4"
    output_filename = os.path.join(desktop_path, f"{title}.mp4")

    # 5. 下载函数
    def download_file(download_url, filename, desc):
        print(f"📥 正在下载 {desc} ...")
        with requests.get(download_url, headers=headers, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"✅ {desc} 下载完成！")

    # 执行下载
    download_file(video_url, video_temp, "【视频画面】")
    download_file(audio_url, audio_temp, "【音频声音】")

    # 6. 音视频混流合成
    print(f"\n🛠️ 正在合成，准备保存至桌面：\n{output_filename}")
    try:
        if os.path.exists(output_filename):
            os.remove(output_filename)  # 覆盖已存在的文件

        ffmpeg_tools.ffmpeg_merge_video_audio(video_temp, audio_temp, output_filename)
        print(f"🎉 大功告成！视频已成功保存到桌面，快去看看吧！")
        print(f"👉 提示：如果 Windows 默认播放器无法播放画面，请使用 PotPlayer 或 VLC 播放！")
    except Exception as e:
        print(f"❌ 合成失败: {e}")
    finally:
        if os.path.exists(video_temp): os.remove(video_temp)
        if os.path.exists(audio_temp): os.remove(audio_temp)

if __name__ == "__main__":
    print("=" * 25)
    print("   B站视频全自动解析下载器 ")
    print("=" * 25)

    while True:
        video_url = input("\n🔗 请粘贴B站视频链接 (输入 q 退出): ").strip()
        if video_url.lower() == 'q':
            break
        if video_url:
            download_bilibili_video(video_url)
