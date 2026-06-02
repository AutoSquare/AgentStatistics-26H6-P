# AgentStatistics

AgentStatistics 是一套用于统计和分析 AI Agent Token 使用情况的管理系统。它可实时监测模型调用量、Token 消耗及费用数据，并通过可视化方式展示运行趋势，为智能体应用的成本控制与性能优化提供支持。

## 功能定位

| 能力 | 说明 |
|------|------|
| 用量监测 | 汇总 Agent 调用次数、各模型 Token 结构（输入、缓存、输出等） |
| 费用分析 | 基于用量与价目规则做费用估算，辅助成本复盘 |
| 趋势可视化 | 按时间维度展示消耗走势与分布，识别峰值与异常 |
| 多维归因 | 按 Agent 任务、Automation、模型、工作区等维度排行与对比 |
| 本地优先 | 数据在本机采集与展示，不上传对话内容与提示词 |

## 技术栈

- **桌面端**：WPF（.NET 8）、MVVM（CommunityToolkit.Mvvm）
- **计算层**：Python（`ASPy/` + 嵌入式 `ASEnv/`）
- **持久化**：memory 模式 — 会话快照与窗口布局写入 `%AppData%/AgentStatistics/`

架构与目录约定见 [docs/开发说明.md](docs/开发说明.md)。

## 环境要求

- Windows 10/11
- [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0)
- Python 3.10+（用于首次创建 `ASEnv/`；也可由脚手架脚本自动安装）

## 快速开始

### 1. 克隆与还原

```powershell
cd AgentStatistics
dotnet restore
```

### 2. Python 环境（若尚无 `ASEnv/`）

在项目根目录执行（需已安装 `wpf-python-mvvm-builder` 技能脚本，或手动创建 venv）：

```powershell
py -3 -m venv ASEnv
.\ASEnv\Scripts\python.exe -m pip install -r ASPy\requirements.txt
```

### 3. 构建与运行

```powershell
dotnet build
dotnet run
```

或在 Visual Studio 中打开 `AgentStatistics.sln`，按 F5 启动。

## 项目结构

```
AgentStatistics/
├── ViewModel/          # 视图模型与命令
├── Services/           # Python 桥、会话、快照与设置
├── Model/              # 领域模型与序列化
├── Themes/             # 全局主题
├── ASPy/               # Python 计算脚本
├── ASEnv/              # 本地 Python 运行时（不提交 git）
├── docs/
│   ├── 开发说明.md     # 持久化、构建与目录职责
│   └── agents/         # Agent 技能配置
├── AGENTS.md           # 工单与领域文档入口
└── README.md
```

## 数据与隐私

- 业务数据默认保存在 `%AppData%\AgentStatistics\`（`session_snapshot.json`、`user_settings.json`）。
- 系统设计上只处理用量元数据（Token 数、模型名、时间戳、Agent 标识等），不读取或上传用户提示词、代码片段与工具输出正文。
- 费用展示为估算结果，不等同于官方账单；实际扣费请以服务商账单为准。

## 开发状态

当前仓库已完成双栈 MVVM 标准脚手架（memory 持久化、Python 桥接、主题与快照接线）。统计面板、图表与数据源适配等业务功能在持续开发中。

参与开发前请阅读 [docs/开发说明.md](docs/开发说明.md)；领域术语与架构决策将写入工作区 [CONTEXT-MAP.md](../CONTEXT-MAP.md) 所指向的 `CONTEXT.md`（按需创建）。

## 相关文档

- [开发说明](docs/开发说明.md) — 构建、持久化与模块职责
- [Agent 技能配置](docs/agents/) — 工单跟踪与领域文档消费规则

## 许可证

待定（发布前补充）。
