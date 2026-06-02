using System.IO;
using System.Text;
using AgentStatistics.Model.Domain;

namespace AgentStatistics.Services;

/// <summary>
/// 当前会话（memory）：内存表 + 快照/autosave，无 ZIP 工程文件。
/// </summary>
public sealed class ProjectSession
{
    private string? _workspaceDirectory;
    private static bool _initialized;

    /// <summary>当前活动会话单例。</summary>
    public static ProjectSession Live { get; } = new();

    /// <summary>内存中的文档。</summary>
    public ProjectDocument Document { get; private set; } = new();

    /// <summary>是否已修改。</summary>
    public bool IsDirty { get; private set; }

    /// <summary>快照中保存的状态栏文本。</summary>
    public string? SavedStatusText { get; private set; }

    /// <summary>构建写入快照的 UI 状态字典。</summary>
    /// <param name="statusText">状态栏文本。</param>
    /// <returns>uiState 键值。</returns>
    public static IReadOnlyDictionary<string, object?> BuildUiState(string statusText) =>
        new Dictionary<string, object?> { ["statusText"] = statusText };

    /// <summary>确保已从快照或默认种子初始化。</summary>
    public void EnsureInitialized()
    {
        if (_initialized)
            return;
        _initialized = true;
        if (SessionSnapshotStore.TryLoad(out var doc, out var statusText))
        {
            Document = doc;
            SavedStatusText = statusText;
        }
        else
            Document = CreateDefaultDocument();
    }

    /// <summary>标记文档已修改。</summary>
    public void MarkDirty() => IsDirty = true;

    /// <summary>退出时将当前表与 UI 状态写入 session_snapshot.json。</summary>
    /// <param name="uiState">可选 UI 状态。</param>
    public void PersistOnExit(IReadOnlyDictionary<string, object?>? uiState = null)
    {
        EnsureInitialized();
        SessionSnapshotStore.Save(Document, uiState);
    }

    /// <summary>计算完成后保存快照（含 UI 状态）。</summary>
    /// <param name="uiState">可选 UI 状态。</param>
    public void SaveSnapshot(IReadOnlyDictionary<string, object?>? uiState = null)
    {
        EnsureInitialized();
        SessionSnapshotStore.Save(Document, uiState);
        IsDirty = false;
    }

    /// <summary>导出 Python 临时工作区。</summary>
    /// <returns>工作区根路径。</returns>
    public string ExportWorkspaceForPython()
    {
        EnsureInitialized();
        if (string.IsNullOrEmpty(_workspaceDirectory))
        {
            _workspaceDirectory = Path.Combine(
                Path.GetTempPath(),
                AppPaths.WorkspacePrefix + Guid.NewGuid().ToString("N"));
        }
        WriteTablesToWorkspace(_workspaceDirectory);
        return _workspaceDirectory;
    }

    /// <summary>从 Python 工作区合并结果。</summary>
    /// <param name="workspaceRoot">工作区根目录。</param>
    public void MergeTablesFromWorkspaceDirectory(string workspaceRoot)
    {
        EnsureInitialized();
        MergeFolder(workspaceRoot, ProjectFileFormat.DataFolder, Document.DataTables);
        MergeFolder(workspaceRoot, ProjectFileFormat.MaterialFolder, Document.MaterialTables);
        IsDirty = true;
    }

    /// <summary>用外部文档替换当前会话。</summary>
    /// <param name="document">文档实例。</param>
    public void ReplaceDocument(ProjectDocument document)
    {
        Document = document;
        _initialized = true;
        IsDirty = true;
    }

    /// <summary>清理 Python 临时工作区。</summary>
    public void CleanupPythonWorkspaceDirectory()
    {
        if (string.IsNullOrEmpty(_workspaceDirectory))
            return;
        try
        {
            if (Directory.Exists(_workspaceDirectory))
                Directory.Delete(_workspaceDirectory, recursive: true);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
        _workspaceDirectory = null;
    }

    private static ProjectDocument CreateDefaultDocument()
    {
        var doc = new ProjectDocument();
        doc.DataTables["项目基本信息"] = "[{\"键\":\"app\",\"值\":\"Agent Statistics\"}]";
        doc.MaterialTables["示例材料"] = "[{\"索引\":1,\"名称\":\"示例材料\"}]";
        return doc;
    }

    private void WriteTablesToWorkspace(string workspaceRoot)
    {
        WriteFolder(workspaceRoot, ProjectFileFormat.DataFolder, Document.DataTables);
        WriteFolder(workspaceRoot, ProjectFileFormat.MaterialFolder, Document.MaterialTables);
    }

    private static void WriteFolder(string workspaceRoot, string folderName, Dictionary<string, string> tables)
    {
        var dir = Path.Combine(workspaceRoot, folderName);
        Directory.CreateDirectory(dir);
        foreach (var pair in tables)
        {
            var path = Path.Combine(dir, pair.Key + ".json");
            File.WriteAllText(path, pair.Value, new UTF8Encoding(false));
        }
    }

    private static void MergeFolder(string workspaceRoot, string folderName, Dictionary<string, string> target)
    {
        var dir = Path.Combine(workspaceRoot, folderName);
        if (!Directory.Exists(dir))
            return;
        foreach (var file in Directory.GetFiles(dir, "*.json"))
        {
            var tableName = Path.GetFileNameWithoutExtension(file);
            target[tableName] = File.ReadAllText(file, Encoding.UTF8);
        }
    }
}
