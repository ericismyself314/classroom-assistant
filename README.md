# 课堂助手 3.0

课堂助手把课堂录音或已有文字稿整理成中文课堂概览、带时间戳的内容和重要事项。课程、录音、转写与模型默认保存在自己的 Windows 电脑上。

## 下载与首次使用

从 [GitHub Releases](https://github.com/ericismyself314/classroom-assistant/releases/latest) 下载 `ClassroomAssistant-3.0.1-win-x64-portable.zip`，解压整个压缩包后运行 `ClassroomAssistant.exe`。请保留旁边的 `_internal` 文件夹。

第一次使用需要联网并准备模型，预留约 2–5 GB 空间：

| 用途 | 模型 | 说明 |
| --- | --- | --- |
| 语音识别 | Qwen3-ASR-0.6B | 本地识别录音；优先使用可用的 NVIDIA GPU，也可使用 CPU（速度较慢） |
| 文字整理 | Qwen3-4B（Q5_K_M）+ llama.cpp | 本地生成中文课堂概览、时间轴和重要事项；建议约 6 GB 显存 |

支持 Windows 10/11 64 位；需要 Microsoft Edge WebView2 Runtime。模型权重不包含在便携包中，避免让程序包膨胀至数 GB；应用会在首次设置时下载并校验。请为模型权重预留约 2–5 GB，Python/Qwen 运行依赖另占磁盘空间。模型准备完成后，日常录音处理默认离线进行。

**语音识别运行环境：**便携程序不要求 Python 才能打开，但 Qwen 语音识别目前由独立的本地 Python 运行环境承载。首次识别前，请安装 64 位 Python 3.12，并运行 `py -3.12 -m pip install "qwen-asr==0.0.6"`；如果电脑已有带 Qwen 依赖的 Python 3.12 环境，可直接使用。没有可用环境时，界面会提示语音服务无法启动。文字整理服务由应用自行下载，不依赖 Python 绑定。

## 主要功能

- 一节课可以按顺序加入多段录音，也可以导入已有文字稿。
- 可直接用电脑麦克风录音、暂停/继续、试听、保存到本机或删除。
- 查看课程周历、中文整理内容、带时间戳的转写、重要事项和跨课程搜索。
- 英文识别原文会保留以便核对；中文展示内容独立保存。

## 隐私

仓库不含真实录音、课程表、转写、数据库、模型权重或密钥。默认情况下，这些数据只保存在 `%LOCALAPPDATA%\ClassroomAssistant`。若用户主动配置外部 OpenAI 兼容服务，发送内容将由该服务处理；详见 [`数据存储与隐私`](docs/数据存储与隐私.md)。

## 从源码运行

需要 Python 3.12、Node.js 20+ 和 Microsoft Edge WebView2 Runtime。安装 `requirements.txt` 中依赖后，在 `frontend` 目录运行 `npm ci` 和 `npm run build`，再从项目根目录运行 `python src/main.py`。详细说明见 [`开发者构建说明`](docs/开发者构建说明.md)。

## 发布说明与许可

3.0.1 的变化见 [`CHANGELOG.md`](CHANGELOG.md)。本项目采用 MIT License；第三方模型、运行库及依赖遵循各自许可证。
