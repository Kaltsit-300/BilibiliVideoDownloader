# B站视频下载器 (Bilibili Video Downloader)

一个现代化的 B站视频下载工具，支持图形界面操作、扫码登录、智能画质选择。

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)

## ✨ 主要特性

- 📱 **扫码登录** - 弹出二维码，用 B站 App 扫码即可获取 Cookie
- 💾 **本地缓存** - Cookie 自动缓存到本地，下次启动直接可用
- 🎯 **最高画质** - 智能选择 HEVC/AV1/AVC 编码，支持 8K/4K/1080P+
- 🔄 **多线程下载** - 界面不卡顿，实时显示进度日志
- 🔗 **日志超链接** - 日志中的 URL 可点击直接打开

## 📦 安装

### 系统要求
- Windows 10/11
- Python 3.8+（推荐 3.14）

### 安装依赖
```bash
pip install -r requirements.txt
```

或使用国内镜像加速：
```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 🚀 快速开始

### 运行程序
```bash
python "Bilibili Video Downloader.py"
```

### 使用步骤
1. **获取 Cookie** - 点击「获取 Cookie」按钮
   - 程序会自动弹出二维码窗口
   - 用 B站 App 扫码登录
   - Cookie 会自动缓存到本地
   
2. **填写链接** - 粘贴 B站视频链接（支持 BV号/短链接/完整URL）
   
3. **选择目录** - 点击「选择文件夹」选择保存位置
   
4. **开始下载** - 点击「开始下载」

## 🔧 技术说明

### Cookie 获取方案

程序采用两层 Cookie 获取策略：

| 优先级 | 方案 | 说明 |
|--------|------|------|
| 1 | 本地缓存 | 扫码成功后自动保存，下次启动直接加载 |
| 2 | 扫码登录 | 弹出二维码，用 B站 App 扫码授权 |

> ⚠️ **注意**：由于 Edge/Chrome v127+ 引入了 App-Bound Encryption (v20) 加密机制，浏览器 Cookie 自动提取方案已不可行，因此移除了该功能。扫码登录是目前最可靠的方式。

### 画质选择策略

程序智能选择最优视频流：

```
编码优先级: HEVC (H.265) > AV1 > AVC (H.264)
画质支持:   8K → 4K → 1080P60 → 1080P+ → 1080P → 720P
```

HEVC 编码画质更好、文件更小，是默认首选。

### Dolby Vision 视频

部分 B站视频采用 Dolby Vision HDR 编码（标题通常含"杜比全景声"），在部分播放器中可能出现颜色问题：
- **PotPlayer**: 深色背景可能发灰 → 切换渲染器为 Direct3D 11
- **VLC/系统播放器**: 正常显示

## 🎨 UI 设计

程序采用现代深色主题设计：

| 元素 | 颜色 |
|------|------|
| 背景 | `#0f172a` 深邃蓝 |
| 卡片 | `#111827` 暗灰 |
| 强调色 | `#f97316` 活力橙 |
| 成功状态 | `#22c55e` 绿色 |
| 日志背景 | `#090f1a` 深黑 |

特性：
- 按钮悬停变色效果
- 日志区 URL 可点击跳转
- Cookie 状态实时显示

## 📁 项目结构

```
BilibiliVideoDownloader/
├── Bilibili Video Downloader.py  # 主程序 (~700行)
├── requirements.txt              # 依赖列表
├── .gitignore                    # Git忽略配置
└── README.md                     # 项目说明
```

## 🛠️ 依赖库

```
requests        - HTTP请求
moviepy         - 音视频合成
pycryptodome    - 加密解密（可选）
browser-cookie3 - 浏览器Cookie（可选，已移除使用）
```

## ❓ 常见问题

**Q: 为什么没有自动从浏览器获取 Cookie？**
A: Edge/Chrome v127+ 使用了 App-Bound Encryption 加密 Cookie，第三方程序无法解密。扫码登录是更可靠的方案。

**Q: 扫码后 Cookie 会过期吗？**
A: B站 Cookie 通常有效期为 30 天。过期后重新扫码即可。

**Q: 下载的视频播放有问题？**
A: 
- 尝试使用 VLC 或 PotPlayer 播放
- PotPlayer 若显示发灰，切换渲染器为 Direct3D 11
- 确保安装了最新版解码器

**Q: 程序无法启动？**
A: 
- 检查 Python 版本是否 3.8+
- 重新安装依赖: `pip install -r requirements.txt --force-reinstall`

## 🔒 安全说明

- Cookie 仅保存在本地 `.bili_cookie_cache` 文件
- 不会上传任何数据到第三方服务器
- 扫码登录使用 B站官方接口

## 📄 许可证

MIT License - 仅供学习研究使用

## ⚠️ 免责声明

本工具仅供个人学习和技术研究使用，请勿用于商业用途或侵犯版权。使用者应遵守 B站服务条款和相关法律法规。

---

Made with ❤️ by [Kaltsit-300](https://github.com/Kaltsit-300)
