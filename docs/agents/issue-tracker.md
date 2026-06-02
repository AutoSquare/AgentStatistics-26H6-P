# 问题跟踪：本地 Markdown

本仓库工单与 PRD 以 `.scratch/` 下 Markdown 文件形式存在。

## 约定

- 每个特性一个目录：`.scratch/<feature-slug>/`
- PRD 为 `.scratch/<feature-slug>/PRD.md`
- 实现工单为 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，自 `01` 编号
- 分拣状态记在各工单文件靠前的 `Status:` 行（角色字符串见 `triage-labels.md`）
- 评论与交流历史追加在文件底部 `## Comments` 标题下

## 当技能要求「发布到问题跟踪」

在 `.scratch/<feature-slug>/` 下新建文件（按需创建目录）。

## 当技能要求「拉取相关工单」

读取指定路径文件。用户通常会直接传入路径或工单编号。
