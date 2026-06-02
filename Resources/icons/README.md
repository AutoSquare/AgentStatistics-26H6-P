# 工程文件图标（archive 可选）

`FileAssociationUtilities` 注册 `DefaultIcon` 时按以下顺序查找：

1. `Resources/icons/project_file.ico`（推荐，Explorer 显示稳定）
2. `Resources/icons/project_file.png`（注册表可写但壳层可能回退为空白文档图标）
3. 当前进程 `exe`（缺省）

## 从 GeoPile 拷贝

```powershell
Copy-Item GeoPile/Resources/icons/project_file.ico Resources/icons/project_file.ico
```

并在 `.csproj` 中确保 `Content` + `CopyToOutputDirectory`（scaffold archive 已含 `Resources/**` 资源项）。

图标内容变更后，下次启动应用会按 SHA256 刷新 HKCU 关联。
