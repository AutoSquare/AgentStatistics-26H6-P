namespace AgentStatistics.Model.Domain;

/// <summary>
/// 内存中的会话文档：数据表与材料库 JSON 表集合。
/// </summary>
public sealed class ProjectDocument
{
    /// <summary>
    /// 初始化空文档。
    /// </summary>
    public ProjectDocument()
    {
        DisplayName = "AgentStatistics";
    }

    /// <summary>界面显示名称。</summary>
    public string DisplayName { get; set; }

    /// <summary>数据表：表名 → JSON 数组文本。</summary>
    public Dictionary<string, string> DataTables { get; } = new(StringComparer.Ordinal);

    /// <summary>材料库：表名 → JSON 数组文本。</summary>
    public Dictionary<string, string> MaterialTables { get; } = new(StringComparer.Ordinal);
}
