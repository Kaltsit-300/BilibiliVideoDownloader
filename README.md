# Bilibili Video Downloader

一个面向 Windows 的 B 站视频下载工具，提供图形界面、扫码登录、合集/分 P 选择、并行下载、进度显示、暂停和取消任务等功能。

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

## 主要功能

- 扫码登录：通过 B 站 App 扫码获取 Cookie。
- Cookie 缓存：扫码成功后缓存到本地，下次启动可复用。
- 合集/分 P 解析：粘贴合集或分 P 视频链接后，可解析出视频列表。
- 多选下载：勾选视频前的复选框即可选择多个视频，一次性启动多个下载任务。
- 并行下载：多个视频可以同时下载，不需要等待前一个完成。
- 任务控制：下载过程中可对选中的任务执行暂停、继续和取消。
- 进度显示：任务列表显示每个下载任务的状态和百分比。
- 智能画质选择：优先选择 HEVC，其次 AV1、AVC，并尽量选择高画质流。
- 原流合并：音频和视频分开下载后合并为 MP4，正常情况下不会二次压缩。
- 可滚动界面：左右分栏界面，视频列表、任务列表、日志区和页面滚动互不干扰。

## 安装

### 系统要求

- Windows 10/11
- Python 3.8+
- 可访问 B 站相关接口

### 安装依赖

```bash
pip install -r requirements.txt
```

如果下载依赖较慢，可以使用国内镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 使用方法

### 启动程序

```bash
python "Bilibili Video Downloader.py"
```

### 基本流程

1. 点击「获取 Cookie」，用 B 站 App 扫码登录。
2. 粘贴 B 站视频链接、合集链接或分 P 视频链接。
3. 点击「解析列表」，等待视频列表加载。
4. 在「选择视频」区域勾选要下载的视频（点击行或复选框均可切换）。
5. 可勾选多个视频。
6. 点击「下载选中视频」开始下载。
7. 在「下载任务」区域查看进度，必要时暂停、继续或取消任务。

如果不点击「解析列表」，程序会尝试按当前链接下载默认视频。

## 界面说明

新版界面采用左右分栏：

- 左侧：链接输入、保存目录、Cookie 获取、视频列表选择。
- 右侧：下载任务列表、暂停/继续/取消按钮、运行日志。

滚动行为：

- 鼠标在视频列表内滚动时，只滚动视频列表。
- 鼠标在下载任务内滚动时，只滚动任务列表。
- 鼠标在日志区内滚动时，只滚动日志。
- 鼠标在页面空白区域滚动时，滚动整个页面。

## 下载质量说明

程序使用 B 站播放接口返回的 DASH 音视频流：

1. 下载视频流。
2. 下载音频流。
3. 使用 ffmpeg 合并为 MP4。

合并过程通常只是封装，不会主动重新编码，所以一般不会产生额外画质或音质损失。最终质量主要取决于：

- 当前账号权限和 Cookie 是否有效。
- B 站接口实际返回的可用画质和音频码率。
- 视频本身是否提供 HEVC、AV1、HDR、杜比等特殊流。
- 本地播放器是否支持对应编码。

### 默认选择策略

```text
编码优先级: HEVC (H.265) > AV1 > AVC (H.264)
常见画质:   8K / 4K / 1080P60 / 1080P+ / 1080P / 720P
```

如果下载后出现偏色、发灰、无画面或无法播放，优先尝试 VLC 或 PotPlayer。部分 HDR/Dolby Vision 视频在播放器不支持时可能显示异常，这通常不是下载损坏。

## Cookie 说明

程序采用两层 Cookie 获取策略：

| 优先级 | 方案 | 说明 |
| --- | --- | --- |
| 1 | 本地缓存 | 扫码成功后保存到 `.bili_cookie_cache` |
| 2 | 扫码登录 | 弹出二维码，用 B 站 App 扫码授权 |

由于 Edge/Chrome v127+ 引入 App-Bound Encryption，浏览器 Cookie 自动提取不稳定，因此当前主要使用扫码登录。

## 项目结构

```text
BilibiliVideoDownloader/
├── Bilibili Video Downloader.py  # 主程序
├── requirements.txt              # Python 依赖
├── README.md                     # 项目说明
└── .gitignore                    # Git 忽略配置
```

## 依赖

```text
requests        HTTP 请求
moviepy         调用 ffmpeg 合并音视频（自带 ffmpeg，无需单独安装）
customtkinter   GUI 界面
Pillow          封面图片显示
```

ffmpeg：合成音视频必须。moviepy 默认自带 ffmpeg；若缺失，程序启动会在日志中告警，
也可手动安装 ffmpeg 并加入系统 PATH。仅下载音频/封面不依赖 ffmpeg。

## 常见问题

### 为什么需要 Cookie？

未登录状态下，B 站通常只返回较低画质。扫码登录后，程序可以请求账号权限范围内的更高画质。

### 可以一次下载多个视频吗？

可以。解析列表后勾选多个视频前的复选框，再点击「下载选中视频」即可。

### 暂停和取消什么时候生效？

暂停和取消会在下载数据块之间生效。合并阶段通常很短，可能无法像下载阶段一样精确暂停。

### 下载的视频是否被二次压缩？

正常情况下不会。程序下载 B 站返回的音视频流，然后合并为 MP4，不主动重新编码。

### 二维码窗口没有显示怎么办？

确认依赖安装完整，并检查网络是否能访问二维码图片服务。如果无法显示二维码，日志里会给出可打开的二维码链接。

## 安全说明

- Cookie 只保存在本地 `.bili_cookie_cache` 文件。
- 程序不会上传 Cookie 到第三方服务器。
- 扫码登录使用 B 站官方接口。
- 请勿把 `.bili_cookie_cache` 上传到公开仓库。

## 免责声明

本工具仅供个人学习和技术研究使用。请遵守 B 站服务条款、版权要求和相关法律法规。请勿用于商业用途或侵犯他人权益。

## License

MIT License

---

Made by [Kaltsit-300](https://github.com/Kaltsit-300)
