# GitHub Pages 使用与部署

本模式面向无需维护后端的个人使用：把课堂录音或文字稿保存在自己的浏览器，回听、搜索并整理笔记。
GitHub Pages 只托管静态页面，不能运行原项目的 Python / SQLite / 本地模型进程。
因此采用独立浏览器入口，默认 Windows 构建和数据格式保持原样。

## 一次设置，后续自动发布

1. Fork 仓库。在 fork 的 Settings → Pages → Build and deployment 中，将 Source 设置为 **GitHub Actions**。
2. 在 Actions 页面启用 fork 的工作流（如果 GitHub 要求）。
3. 进入 **Deploy browser app to Pages**，点击 Run workflow，选择含本改动的分支。
4. 完成后打开部署输出链接，一般为 `https://<用户名>.github.io/classroom-assistant/`。
5. 合并至 `main` 后，每次向 `main` 推送都会重新发布；其他分支仅手动部署。PR 检查不会部署站点。

仓库管理员只需设置一次发布源，运行期间无需付费 API、服务端密钥或常驻服务器。
网站更新仍取决于 GitHub Pages 和 Actions 的服务可用性，并非托管保障承诺。
参考：[GitHub 官方发布源说明](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)。

## 使用

1. 新建课堂，填写课程名称、日期和标题。
2. 选择录音，或粘贴 / 导入 UTF-8 TXT 文字稿。
3. 可展开“在本机自动转写”，选择语言，再主动下载模型并转写。录音由 Web Audio 解码并重采样到 16 kHz，Whisper tiny 在 Worker 中以单线程 WASM 运行，不要求跨源隔离响应头。
4. 识别完成后核对文字，点击“提取原文与事项”获得规则摘录，或自己编写笔记。摘录只展示部分开头原文和关键词匹配句，不承诺覆盖所有知识点。完整文字稿始终保留。
5. 点击“保存课堂”。显示保存成功后，刷新仍可找到课堂和录音。未保存修改在离开时会提示。
6. 在时间轴点击时间戳跳到对应录音；支持 `[mm:ss]`、`[hh:mm:ss]`。无时间戳的段落不会编造时间。

### 语音识别边界

- 首次使用需访问 Hugging Face 下载 `Xenova/whisper-tiny` 模型，运行文件随站点发布；下载量约数百 MB，模型可被浏览器缓存。清缓存后需重新下载。
- 下载请求会向模型托管方暴露通常的网络元数据，但录音和文字稿不会随模型请求上传。无 API key、分析统计或项目服务端。
- 自动转写是实验功能，小模型的中文、混合语言和远距离课堂录音质量有限；不能以短样本通过等同于完整课堂准确率。
- 文件上限 200 MB，解码后时长上限 2 小时；浏览器仍需足够内存，低内存设备建议分段或用 Windows 版。
- 解码格式由浏览器决定，WAV / MP3 较通用；遇到 M4A / MP4 等解码失败可转换后重试。
- 取消会终止 Worker，原文字稿保留；识别结果要手动保存。失败后可重试或导入现有文字稿。
- 不提供网页离线启动 / Service Worker，也不承诺模型服务器长期可达；文字整理不依赖模型下载。

技术参考：[Transformers.js pipeline 文档](https://huggingface.co/docs/transformers.js/en/pipelines)。

推理库固定为 Transformers.js 3.8.1：实测 4.2.0 搭配本模型 q8 权重在 WASM 加载时出现
`Missing required scale`，不能仅依据构建成功就升级。升级需要重新执行真实音频测试。

## 数据与备份

- 记录存于 IndexedDB，按网站项目路径区分。不同域名、浏览器、隐私模式、设备之间不自动同步。网站地址改变前请导出。
- 保存成功仅在 IndexedDB 事务提交后显示；存储失败会提示错误。
- **导出文字备份**：已保存课堂的标题、课程、日期、完整文字和笔记，JSON 文件不含录音。
- **下载原录音**：每节课堂单独下载原始音频文件。
- **导入网页版文字备份**：先验证整个 v1 JSON，再以单个事务添加为新记录，避免覆盖现有课堂和录音。重复导入会产生重复记录。恢复后可重新附加相应录音。
- **导出 Markdown**：导出当前编辑内容，适合复习和分享。
- 浏览器清除数据、磁盘空间不足或自动回收网站数据均可能影响记录，浏览器存储不是永久备份。不要将私人备份提交到公开仓库。

## 从源码运行与检查

需要 Node.js 22.12+（CI 使用 Node 22）。

```sh
cd frontend
npm ci
npm run dev:pages
# 发布产物：frontend/dist-pages
npm run typecheck
npm run build:pages
# 原桌面产物仍在 frontend/dist
npm run build
npx playwright install chromium
npm run test:e2e
```

E2E 覆盖桌面与手机视口、仓库子路径资源、无后端初始化、保存刷新、搜索、时间轴、
录音持久化、备份恢复、无效备份原子性、取消、删除，以及桌面版断线反馈。
普通 CI 不下载语音模型；真实模型质量和完整课堂性能需另行验证。

Vite 更新到 6.4.3 修复原版本开发服务器告警；`sharp` 和 `adm-zip` overrides
修复 Transformers.js 安装链中的 Node 依赖告警，这些 Node 组件不会进入浏览器运行包。
