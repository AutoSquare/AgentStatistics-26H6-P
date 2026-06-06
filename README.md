# AgentStatistics

AgentStatistics 是一个面向 Windows 桌面的本地 AI Agent 用量统计工具。它通过 WPF 承载 WebView2 仪表盘，读取本机 Codex、Cursor、Antigravity 用量数据，展示 Token 消耗、模型分布、调用趋势、缓存命中、失败状态、额度风险和费用估算等用量元数据。

项目强调本地优先：统计过程只处理 Codex 会话日志中的用量元数据，不读取、不导出、不上传用户提示词、助手正文、工具输出或代码片段。

## 功能定位

| 能力 | 说明 |
| --- | --- |
| Codex 用量监测 | 扫描本机 Codex sessions JSONL 日志，聚合请求数、模型、Token 类型与时间范围视图。 |
| Cursor 用量监测 | 解析官网 Dashboard 导出的 `usage.json` / tokscale `cursor-cache` CSV，通过隐藏 WebView2 复用登录态同步用量与 usage-summary 额度；按账号隔离历史、合并官网与本地记录，并支持全部账号或单账号查看。 |
| Antigravity 用量监测 | 读取 `antigravity-cache/sessions/*.jsonl` 与 `~/.gemini/antigravity-cli` transcript；刷新时从运行中 CLI（agy）经 Connect RPC 同步；可选配额风险面板。 |
| 趋势可视化 | 使用 Vue、Vite 和 ECharts 展示 Token 趋势、调用分布、费用结构和风险状态。 |
| 本地路径配置 | 支持在界面中配置 Codex sessions 路径，并通过 WPF 监听日志变化触发刷新。 |
| 桌面集成 | 使用 WPF + WebView2 提供桌面应用壳，支持窗口布局与会话状态持久化。 |
| 隐私边界 | 默认只处理用量元数据；业务快照和用户设置写入 `%AppData%/AgentStatistics/`。 |

## 技术栈

- 桌面端：WPF、.NET 8、WebView2、MVVM
- 前端：Vue 3、Vite、TypeScript、ECharts、Lucide Vue
- 统计层：Python，位于 `ASPy/`
- 发布运行时：便携 Python 3.10.11，位于 `ASEnv/`；`ASEnv` 必须是可移植环境，不能是 venv
- WebView2 运行时：安装包携带 Microsoft Evergreen Standalone Installer，目标机缺失时由安装器静默安装
- 持久化：memory 模式，窗口布局、路径设置和会话快照写入 `%AppData%/AgentStatistics/`

## 快速开始

### 1. 还原并构建 WPF 项目

```powershell
cd AgentStatistics
dotnet restore
```

### 2. 构建 WebClient

```powershell
cd WebClient
npm install
npm run build
cd ..
```

### 3. 构建并运行桌面应用

```powershell
dotnet build
dotnet run
```

发布前先准备 `ASEnv/` 便携运行时，并确认其中没有 `pyvenv.cfg` 或开发机绝对路径；安装包还必须携带 `ThirdParty/WebView2/MicrosoftEdgeWebView2RuntimeInstallerX64.exe`，不能要求最终用户手动安装 WebView2。

也可以在 Visual Studio 中打开 `AgentStatistics.sln`，按 F5 启动。

## Codex 统计链路

默认读取路径：

```text
%USERPROFILE%\.codex\sessions
```

如果 Codex sessions 位于其他目录，可以在 AgentStatistics 的 Codex 页顶部修改 `Codex sessions` 路径并保存。应用会：

1. 将路径设置保存到本地用户设置；
2. 监听该目录下的 `*.jsonl` 文件变化；
3. 调用统计服务生成前端 payload；
4. 通过 WebView message 将统计结果推送到仪表盘。

当前 Codex 页展示以下视图：

- 今天、24 小时、7 天、30 天、历史范围筛选；
- 总 Token、输入、输出、缓存命中、估算费用 KPI；
- Token 趋势、调用分布、费用结构、额度与风险；
- 会话 / 项目排行、模型排行；
- 当前范围 CSV 导出。

Cursor 页使用相同的时间范围和统计视图，并在时间范围下方显示账号卡片。不同账号的用量相加形成“全部账号”总计；选择单个账号后，KPI、趋势、排行和 CSV 导出均切换到该账号。

## 项目结构

```text
AgentStatistics/
├── ASPy/                  # Python 统计与计算脚本
├── Model/                 # 领域模型与序列化对象
├── Properties/            # Settings.settings 与资源设计器
├── Resources/             # 图标、背景、字体等 WPF 资源
│   └── icons/             # logo.ico、logo.png 与图标说明
├── Services/              # WebView 通信、路径、快照、Codex 统计服务
├── Themes/                # WPF 全局主题
├── ViewModel/             # 主窗口视图模型与命令
├── WebClient/             # Vue + Vite + ECharts 仪表盘源码和构建产物
├── docs/                  # 开发说明与 Agent 文档
├── AGENTS.md              # 工单与领域文档入口
└── README.md
```

## 数据与隐私

- 本项目只面向本地使用，不需要服务端账号或托管遥测。
- Codex 统计只提取 Token 数、模型名、时间戳、会话标识、cwd basename、rate limit、耗时和失败状态等元数据。
- 本项目不读取、不展示、不导出提示词、助手正文、工具输出和文件内容。
- 本地业务数据默认写入 `%AppData%\AgentStatistics\`，包括 `session_snapshot.json`、`user_settings.json` 和 Codex 用量缓存。
- Cursor 官网完整快照按账号归档到 `cursor-cache/accounts/<account-hash>/`。旧版无账号 `usage.csv` 首次迁移时归入当前登录账号；官网分页未拉全时保留上一次完整快照。
- 费用展示为基于本地规则的估算结果，不等同于服务商官方账单。

## 品牌与资源

- 应用名称固定写作 `AgentStatistics`，不拆写为 `Agent Statistics`。
- 侧栏副标题使用 `AutoSquare`。
- WPF 应用图标使用 `Resources/icons/logo.ico`。
- Web 仪表盘品牌标识使用 `WebClient/public/logo.png`，来源为 `Resources/icons/logo.png`。

## 致谢与来源

AgentStatistics 的 Codex 用量解析思路、字段语义和仪表盘数据建模参考并移植自开源项目 [JUk1-GH/CodexScope](https://github.com/JUk1-GH/CodexScope)。

CodexScope 是一个本地优先的 Codex 用量仪表盘，核心能力包括从本地 Codex session logs 提取用量元数据、生成 Token 趋势、额度风险、会话排行、模型排行、调用分布、缓存命中率和费用估算等视图。AgentStatistics 在其基础思路上做了桌面化集成和技术栈迁移：原始 Go/静态网页链路被迁移为 Python 统计层、WPF/WebView2 宿主和 Vue/ECharts 前端。

上游索引：

- 项目主页：[JUk1-GH/CodexScope](https://github.com/JUk1-GH/CodexScope)
- 数据生成参考：[`generate_codex_data.go`](https://github.com/JUk1-GH/CodexScope/blob/main/generate_codex_data.go)
- 仪表盘逻辑参考：[`app.ts`](https://github.com/JUk1-GH/CodexScope/blob/main/app.ts)
- 许可证：CodexScope 仓库标注为 MIT license；使用、修改和再分发时应遵守其许可证要求。

感谢 CodexScope 作者 JUk1-GH 对本地 Codex 用量分析场景的开源贡献。

## 开发文档

- [开发说明](docs/开发说明.md)：目录职责、Codex 统计链路、持久化方式、构建步骤和 UI 约束。
- [Agent 文档](docs/agents/)：工单跟踪、分拣标签和领域文档消费规则。

## 当前状态

当前版本已完成 AgentStatistics 的桌面应用骨架、Codex 用量统计链路、Web 仪表盘、品牌图标接入、本地设置和快照持久化。通用文件分析与跨 Agent 总计页仍保留为后续扩展入口，尚未接入实际数据源。
