# 课堂助手（Classroom Assistant）

一款**完全本地运行**的 Windows 桌面应用，用于把课堂录音 / 文字转写自动整理成结构化的课堂概览与「重要事项」，并支持按周、按课程、按关键词检索。语音识别与文字分析均在**本机完成**，断网也能用。

> 设计目标：真实可用、隐私优先、离线优先。所有模型、数据、日志都保存在你自己的电脑上，不上传任何云端。

## 先用起来（普通用户）

1. 双击 `ClassroomAssistant-Setup-x64.exe` 安装，安装器默认在桌面创建“课堂助手”。
2. 双击桌面图标。第一次打开时，按向导点击“一键准备离线模型（推荐）”，等两项模型准备完成。
3. 在“本周”建立学期和课程，点进课程后上传录音（也可以导入转写文字）。
4. 处理完成后查看课堂概览、重要事项和带时间戳的原文；完成作业或事项后，在“重要事项”页勾选“已完成”。

以后每次只需：**点课程 → 传录音 → 看重点**。数据默认保存在 `%LOCALAPPDATA%\ClassroomAssistant`，不上传云端。

完整的非技术安装步骤见《安装说明（普通用户）》。

---

## 一、它能做什么

- **真实本地语音识别**：基于 `faster-whisper`，首用自动下载模型（快速 / 平衡 / 准确三档），支持中文。
- **真实本地大模型分析**：基于 `llama.cpp` 自管的本地推理服务（Qwen2.5 系列 GGUF），把转写文本提炼成：
  - 课堂概览（主题 + 小结）
  - 重要事项（考试 / 作业 / 调课 / 活动 / 其它），并附**原始证据片段与时间戳**
- **五页导航**：本周 / 课程 / 重要事项 / 搜索 / 设置。
- **学期与课表管理**：学期、课程、上课时间（星期+节次+起止周），自动生成「本周课表」。
- **两种录入方式**：上传录音（自动转写+分析）或导入文字（粘贴带时间戳/不带时间戳的讲稿）。
- **全文搜索**：基于 SQLite FTS5（trigram 分词器），可搜中文子串，跨课堂、转写、重要事项。
- **五层证据核查**：模型输出的重要事项要经过独立验证关卡（结构 → 原文存在性（硬关卡，引用必须在真实转写里找得到）→ 上下文分类 → 语言信号 → 展示），编造的引用直接判 `rejected` 且不进搜索索引。
- **防幻觉机制**：绝不把「学生的提问」「老师的举例」当作已确认的通知（见「重要事项提取」小节）。
- **模型手动导入**：网络下载被阻断时，可导入本地 zip / `.gguf`（严格防 Zip-Slip 路径穿越）。
- **一键自检 / 就绪检查**：`--self-test`（环境与依赖）与 `--readiness-check`（真实加载 + 试转写，按退出码区分结果）。
- **诊断包导出**：设置页可导出诊断包，**不含**音频、转写、密钥、模型。
- **单实例保护**：再次启动应用会聚焦已有窗口，不会开出第二个实例。
- **数据备份与恢复**：一键导出 ZIP 备份（含数据库 + 音频 + 转写），可恢复。
- **隐私保护**：OpenAI 兼容密钥等敏感信息使用 Windows DPAPI 加密存储，绝不写入明文数据库。

---

## 二、技术架构

| 层 | 选型 |
|----|------|
| 桌面窗口 | `pywebview`（WebView2，原生窗口承载前端） |
| 前端 | React 18 + TypeScript + Vite（`frontend/`） |
| 后端 | Python 3.13 标准库 `http.server`（同进程内本地 API，仅监听 `127.0.0.1`） |
| 语音识别 | `faster-whisper` 1.2.1（CTranslate2，支持 GPU/CPU） |
| 文字分析 | `llama.cpp` sidecar（`llama-server.exe` + Qwen2.5 GGUF，仅监听 `127.0.0.1`，退出时关闭） |
| 数据库 | SQLite（WAL 模式 + FTS5 trigram 全文索引） |
| 敏感信息 | Windows DPAPI（`backend/credential.py`） |

后端同时支持三种 LLM 提供方（设置页可切换）：
1. **自动（默认）**：优先 sidecar，其次本机 Ollama，再次 OpenAI 兼容 API。
2. **Ollama**：本机已装 Ollama 时直接对接。
3. **OpenAI 兼容**：填入 base_url + 密钥（密钥 DPAPI 加密）。

---

## 三、目录结构

```
classroom-assistant/
├─ run.py                 # 启动器（python run.py / python run.py --no-gui）
├─ src/
│  ├─ main.py             # 应用入口：初始化、起后端、开窗口、清理
│  └─ backend/            # 后端全部模块
│     ├─ api.py           # HTTP API + 静态资源服务
│     ├─ db.py            # SQLite 封装（WAL/FTS5/事务）
│     ├─ pipeline.py      # 处理流水线（转写→分析→落库→索引）
│     ├─ ai/              # whisper / llama.cpp sidecar / 提示词 / 解析
│     │   ├─ verification.py    # 五层证据核查关卡
│     │   └─ model_import.py    # 手动导入（防 Zip-Slip）
│     ├─ selftest.py      # --self-test / --readiness-check
│     ├─ single_instance.py # 单实例保护
│     ├─ diagnostics.py   # 诊断包导出
│     ├─ schedule.py      # 周课表生成
│     ├─ dateparse.py     # 中文相对日期解析（下周五/明天/十月二十日）
│     ├─ schemas.py       # 模型输出校验与归一化
│     ├─ important.py     # 重要事项去重合并
│     ├─ backup.py        # 备份/恢复
│     ├─ credential.py    # DPAPI 加密
│     └─ ...
├─ frontend/              # React + Vite 前端（npm run build → dist/）
├─ tests/                 # 单元 / 集成 / 打包后冒烟 / 真实模型端到端测试
├─ installer/             # Inno Setup(.iss) / NSIS(.nsi) 安装包脚本
├─ docs/                  # 本文档及安装、构建、隐私、已知问题说明
├─ requirements.txt       # Python 依赖
├─ build_exe.spec         # PyInstaller 打包配置
├─ build_exe.bat          # 仅打包 exe
├─ verify_windows.bat/.ps1  # 一键自检（优先项目 venv→上级 venv→系统 python）
└─ build_release.ps1      # 最终交付一键构建（测试→打包→安装包→SHA256）
```

---

## 四、快速开始（开发者）

```bash
# 1. 创建并激活虚拟环境（Python 3.13）
python -m venv venv
venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 构建前端
cd frontend && npm install && npm run build && cd ..

# 4. 启动（打开桌面窗口）
python run.py

# 或仅启动后端（无界面，方便调试），会打印本地地址
python run.py --no-gui
```

首次使用在「设置」页下载语音模型与文字模型后即可开始。

---

## 五、重要事项提取（防幻觉）

提取规则由 `backend/ai/prompts.py` 与 `backend/schemas.py` 共同约束：

- 每条重要事项必须附 `evidence`（原始片段引用，含 `segment_id`、起止时间、`quote`）。
- **学生提问但未获老师确认** → 不生成事项（如「有同学问作业是不是不用交，老师没回答」）。
- **老师明确说是「举例」** → 不生成考试/通知事项（如「比如考试可能在十月，这只是举例」）。
- 相对日期（「下周五」「明天」「十月二十日」）由 `dateparse.py` 结合课堂日期真实解析为 `YYYY-MM-DD`，并保留 `raw_date_text` 供核对。

这些规则由 `tests/test_integration.py` 中的接受用例 + 两个反面用例（anti-case）覆盖。

### 5.1 五层证据核查（模型输出之后的一道独立关卡）

即使提示词约束了，本地小模型仍可能编造引用。因此 `backend/ai/verification.py` 在**模型输出之后**再逐条验证：

| 层 | 内容 | 失败后果 |
|---:|------|----------|
| L1 | 结构校验（字段、类型、枚举） | `rejected` |
| L2 | **原文存在性（硬关卡）**：引用必须在真实转写原文里找得到；段号缺失或错位时全扫描兜底 | 找不到 → `rejected` |
| L3 | 上下文分类（教师通知 / 学生提问 / 举例…） | 提问或举例 → 不生成或降级 |
| L4 | 语言信号（仅辅助，不单独定罪） | 降低置信度 |
| L5 | 展示层：`confirmed` / `pending` / `rejected` | 默认只展示 confirmed + pending |

若模型后文明确更正/撤回前述内容（`correction_or_retraction` 且带通知信号），以最后表述为准，并覆盖同标题旧条目（`superseded_by_correction`）。`rejected` 事项**不会进入搜索索引**。

---

## 六、自检、就绪检查与打包

```bash
# 环境自检（数据目录、数据库迁移、后端健康检查、前端产物、WebView2、模型状态、GPU）
ClassroomAssistant.exe --self-test        # 退出码 0=通过，2=致命项失败

# 就绪检查（真实加载模型 + 试转写）
ClassroomAssistant.exe --readiness-check
# 0=就绪(GPU) / 10=基础通过但模型缺失 / 11=语音不可用 / 12=文字不可用
# 13=就绪(CPU) / 20=数据库或核心错误 / 21=前端缺失 / 22=后端启动失败
```

最终交付构建（一条命令跑完 测试 → 打包 → 安装包 → SHA256 → 报告）：

```powershell
powershell -ExecutionPolicy Bypass -File build_release.ps1          # Inno Setup
powershell -ExecutionPolicy Bypass -File build_release.ps1 -UseNsis # 或 NSIS
```

对已构建的 exe 做冒烟验证：

```bash
python tests/post_package_check.py
```

> 打包时注意：PyInstaller 会先清空 `build/` 与 `dist/`，若这两个目录已存在且文件很多，
> 需先手动删除，否则会被批量删除保护拦住（`build_release.ps1` 的第 1 步已包含清理）。

---

## 七、文档索引

- [安装说明（普通用户）](安装说明（普通用户）.md)
- [开发者构建说明](开发者构建说明.md)
- [数据存储与隐私](数据存储与隐私.md)
- [已知问题与实际测试结果](已知问题与实际测试结果.md)
