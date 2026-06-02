using System.IO;
using System.Text;
using System.Text.Json;
using AgentStatistics.Model.Domain;

namespace AgentStatistics.Services;

/// <summary>
/// 读写 session_snapshot.json（memory 模式跨会话业务表记忆）。
/// </summary>
public static class SessionSnapshotStore
{
    /// <summary>会话快照文件路径。</summary>
    public static string SnapshotFilePath =>
        Path.Combine(UserSettingsStore.AppDataDirectory, "session_snapshot.json");

    /// <summary>
    /// 将会话文档写入快照文件。
    /// </summary>
    /// <param name="document">内存文档。</param>
    /// <param name="uiState">可选 UI 状态。</param>
    public static void Save(ProjectDocument document, IReadOnlyDictionary<string, object?>? uiState = null)
    {
        Directory.CreateDirectory(UserSettingsStore.AppDataDirectory);
        var payload = new
        {
            formatVersion = 1,
            displayName = document.DisplayName,
            dataTables = document.DataTables,
            materialTables = document.MaterialTables,
            uiState = uiState ?? new Dictionary<string, object?>(),
        };
        var json = JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true });
        var tmp = SnapshotFilePath + ".tmp";
        File.WriteAllText(tmp, json, new UTF8Encoding(false));
        File.Move(tmp, SnapshotFilePath, overwrite: true);
    }

    /// <summary>
    /// 尝试从快照恢复文档与 UI 状态。
    /// </summary>
    /// <param name="document">输出文档。</param>
    /// <param name="statusText">输出状态栏文本；无则 null。</param>
    /// <returns>成功返回 true。</returns>
    public static bool TryLoad(out ProjectDocument document, out string? statusText)
    {
        document = new ProjectDocument();
        statusText = null;
        if (!File.Exists(SnapshotFilePath))
            return false;
        try
        {
            var text = File.ReadAllText(SnapshotFilePath, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            if (text.Length > 0 && text[0] == '\uFEFF')
                text = text[1..];
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            if (root.TryGetProperty("displayName", out var dn))
                document.DisplayName = dn.GetString() ?? document.DisplayName;
            if (root.TryGetProperty("dataTables", out var dt))
                CopyTables(dt, document.DataTables);
            if (root.TryGetProperty("materialTables", out var mt))
                CopyTables(mt, document.MaterialTables);
            if (root.TryGetProperty("uiState", out var uiState) &&
                uiState.TryGetProperty("statusText", out var statusElement) &&
                statusElement.ValueKind == JsonValueKind.String)
            {
                statusText = statusElement.GetString();
            }
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static void CopyTables(JsonElement obj, Dictionary<string, string> target)
    {
        foreach (var prop in obj.EnumerateObject())
            target[prop.Name] = ReadTablePayload(prop.Value);
    }

    /// <summary>
    /// 读取 dataTables 条目：字符串表用 GetString，对象/数组用 RawText；并剥离历史错误加载产生的多余引号层。
    /// </summary>
    /// <param name="element">JSON 元素。</param>
    /// <returns>表 JSON 文本。</returns>
    private static string ReadTablePayload(JsonElement element)
    {
        var payload = element.ValueKind switch
        {
            JsonValueKind.String => element.GetString() ?? string.Empty,
            JsonValueKind.Object or JsonValueKind.Array => element.GetRawText(),
            _ => element.GetRawText(),
        };
        return UnwrapOverQuotedJson(payload);
    }

    private static string UnwrapOverQuotedJson(string payload)
    {
        var current = payload;
        for (var i = 0; i < 4; i++)
        {
            if (current.Length < 2 || current[0] != '"' || current[^1] != '"')
                break;
            try
            {
                var inner = JsonSerializer.Deserialize<string>(current);
                if (string.IsNullOrEmpty(inner) || inner == current)
                    break;
                current = inner;
            }
            catch (JsonException)
            {
                break;
            }
        }
        return current;
    }
}
