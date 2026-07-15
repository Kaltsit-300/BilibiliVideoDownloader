# Bilibili Video Downloader

一个面向 Windows 的 B 站视频下载器，提供图形界面、扫码登录、合集/分 P 解析、多选并行下载、进度显示、任务暂停/取消，以及音视频流合并为 MP4 的完整流程。

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

## 主要功能

- **扫码登录**：通过 B 站 App 扫码获取 Cookie，无需手动抓包。
- **Cookie 缓存与校验**：扫码成功后缓存到本地；下次启动复用，并对 Cookie 做有效性校验（必须含 `SESSDATA`，避免残缺 Cookie 误判为可用）。
- **合集 / 分 P 解析**：粘贴视频、合集或分 P 链接后，解析出可选视频列表。
- **解析兜底**：页面结构变化导致 HTML 解析失败时，自动改用 B 站官方 `view` 接口获取分 P，抗改版更稳。
- **多选并行下载**：勾选多个视频即可一次性启动，默认最多 3 个任务并发。
- **任务控制**：下载中可对选中任务执行暂停、继续、取消。
- **进度显示**：任务列表展示每个任务的实时状态与百分比。
- **真实画质探测**：只把接口实际返回的 DASH/durl 流标为可选，并发探测（避免「显示有却下不了」），不会把账号不支持的画质偷偷降级。
- **智能编码选择**：优先 HEVC (H.265) > AV1 > AVC (H.264)，并尽量选高画质流。
- **原流合并**：音视频分离下载后用 ffmpeg 封装为 MP4，通常不二次压缩。
- **启动时 ffmpeg 自检**：程序启动时检测 ffmpeg 是否可用，缺失时在日志告警，避免下载到合并阶段才报错。
- **左右分栏界面**：视频列表、任务列表、日志区各自独立滚动。

## 安装

### 系统要求

- Windows 10 / 11
- Python 3.8+
- 可访问 B 站相关接口

### 安装依赖

```bash
pip install -r requirements.txt
```

若下载较慢，可使用国内镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 关于 ffmpeg

合并音视频必须依赖 ffmpeg。本程序优先使用 `moviepy` 自带的 ffmpeg；若环境中缺失，启动时会**在日志中明确告警**，也可手动安装 ffmpeg 并加入系统 `PATH`。仅下载音频或封面不依赖 ffmpeg。

## 使用方法

### 启动程序

```bash
python "Bilibili Video Downloader.py"
```

### 基本流程

1. 点击「获取 Cookie」，用 B 站 App 扫码登录。
2. 粘贴 B 站视频 / 合集 / 分 P 链接。
3. 点击「解析列表」，等待视频列表加载（解析失败会自动走官方接口兜底）。
4. 在「选择视频」区域勾选要下载的视频（点击整行或复选框均可切换，可多选）。
5. 点击「下载选中视频」开始下载。
6. 在「下载任务」区域查看进度，必要时暂停、继续或取消任务。

若不点击「解析列表」，程序会尝试按当前链接直接下载默认视频。

## 界面说明

新版界面采用左右分栏：

- **左侧**：链接输入、保存目录、Cookie 获取、视频列表选择。
- **右侧**：下载任务列表、暂停 / 继续 / 取消按钮、运行日志。

滚动行为各自独立：视频列表、下载任务、日志区分别只在各自区域内滚动；页面空白区域滚动整个页面。

## 下载质量说明

程序使用 B 站播放接口返回的 DASH 音视频流，分别下载视频流与音频流，再用 ffmpeg 合并为 MP4。合并过程通常只是封装、不会主动重新编码，因此一般不会产生额外画质或音质损失。最终质量主要取决于：

- 当前账号权限与 Cookie 是否有效；
- B 站接口实际返回的可用画质与音频码率；
- 视频本身是否提供 HEVC、AV1、HDR、杜比等特殊流；
- 本地播放器是否支持对应编码。

### 默认选择策略

```text
编码优先级: HEVC (H.265) > AV1 > AVC (H.264)
常见画质:   8K / 4K / 1080P60 / 1080P+ / 1080P / 720P
```

若下载后出现偏色、发灰、无画面或无法播放，优先尝试 VLC 或 PotPlayer。部分 HDR / Dolby Vision 视频在播放器不支持时可能显示异常，这通常不是下载损坏。

## Cookie 说明

程序采用两层 Cookie 获取策略：

| 优先级 | 方案 | 说明 |
| --- | --- | --- |
| 1 | 本地缓存 | 扫码成功后保存到 `.bili_cookie_cache` |
| 2 | 扫码登录 | 弹出二维码，用 B 站 App 扫码授权 |

受 Edge / Chrome v127+ 的 App-Bound Encryption 影响，浏览器 Cookie 自动提取不稳定，因此当前以扫码登录为主。Cookie 校验要求至少包含 `SESSDATA` 及 `bili_jct` / `DedeUserID` 之一，避免残缺 Cookie 被误判为可用。

## 项目结构

```text
BilibiliVideoDownloader/
├── Bilibili Video Downloader.py  # 主程序（GUI + 下载逻辑）
├── requirements.txt              # Python 依赖
├── README.md                     # 项目说明
└── .gitignore                    # Git 忽略配置
```

### 依赖

```text
requests        HTTP 请求
moviepy         调用 ffmpeg 合并音视频（自带 ffmpeg，无需单独安装）
customtkinter   GUI 界面
Pillow          封面图片显示
```

## 常见问题

### 为什么需要 Cookie？

未登录状态下，B 站通常只返回较低画质。扫码登录后，程序可请求账号权限范围内的更高画质。

### 可以一次下载多个视频吗？

可以。解析列表后勾选多个视频前的复选框，再点击「下载选中视频」即可，默认最多 3 个任务并发。

### 暂停和取消什么时候生效？

暂停 / 取消会在下载数据块之间生效。合并阶段通常很短，可能无法像下载阶段一样精确暂停。

### 下载的视频是否被二次压缩？

正常情况下不会。程序下载 B 站返回的音视频流后合并为 MP4，不主动重新编码。

### 启动后日志提示 ffmpeg 不可用？

说明当前环境未找到 ffmpeg。安装 ffmpeg 并加入系统 `PATH`，或确认 `moviepy` 安装完整（其自带 ffmpeg）即可。

### 二维码窗口没有显示怎么办？

确认依赖安装完整、网络可访问二维码图片服务。若仍无法显示，日志里会给出可手动打开的二维码链接。

## 安全说明

- Cookie 仅保存在本地 `.bili_cookie_cache` 文件。
- 程序不会上传 Cookie 到第三方服务器。
- 扫码登录使用 B 站官方接口。
- 请勿把 `.bili_cookie_cache` 上传到公开仓库（已纳入 `.gitignore`）。

## 免责声明

本工具仅供个人学习与技术研究使用。请遵守 B 站服务条款、版权要求及相关法律法规，勿用于商业用途或侵犯他人权益。

## License

MIT License

---

Made by [Kaltsit-300](https://github.com/Kaltsit-300)
