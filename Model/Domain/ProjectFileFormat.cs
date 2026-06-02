namespace AgentStatistics.Model.Domain;

/// <summary>
/// 表目录常量（memory 模式，无工程文件扩展名）。
/// </summary>
public static class ProjectFileFormat
{
    /// <summary>持久化路线标识。</summary>
    public const string PersistenceMode = "memory";

    /// <summary>数据表目录名。</summary>
    public const string DataFolder = "数据表";

    /// <summary>材料库目录名。</summary>
    public const string MaterialFolder = "材料库";

    /// <summary>样本结果表名。</summary>
    public const string SampleResultTable = "样本计算结果";
}
