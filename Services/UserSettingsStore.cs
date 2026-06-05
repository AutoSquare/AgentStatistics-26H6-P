using System.IO;
using System.Text;
using System.Text.Json;

namespace AgentStatistics.Services;

/// <summary>
/// 读写 %AppData%/{AppId}/user_settings.json（UI 偏好记忆文件）。
/// </summary>
public static class UserSettingsStore
{
    /// <summary>应用数据目录。</summary>
    public static string AppDataDirectory =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "AgentStatistics");

    /// <summary>WebView2 用户数据目录，避免在程序安装目录写缓存。</summary>
    public static string WebView2UserDataDirectory => Path.Combine(AppDataDirectory, "WebView2");

    /// <summary>用户设置文件路径。</summary>
    public static string SettingsFilePath => Path.Combine(AppDataDirectory, "user_settings.json");

    /// <summary>
    /// 加载用户设置 JSON 对象。
    /// </summary>
    /// <returns>键值对；文件不存在时返回空字典。</returns>
    public static Dictionary<string, JsonElement> Load()
    {
        if (!File.Exists(SettingsFilePath))
            return new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        var text = File.ReadAllText(SettingsFilePath, Encoding.UTF8);
        if (text.Length > 0 && text[0] == '\uFEFF')
            text = text[1..];
        using var doc = JsonDocument.Parse(text);
        var result = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        foreach (var prop in doc.RootElement.EnumerateObject())
            result[prop.Name] = prop.Value.Clone();
        return result;
    }

    /// <summary>
    /// 保存用户设置。
    /// </summary>
    /// <param name="settings">设置键值。</param>
    public static void Save(IReadOnlyDictionary<string, object?> settings)
    {
        Directory.CreateDirectory(AppDataDirectory);
        var json = JsonSerializer.Serialize(settings, new JsonSerializerOptions { WriteIndented = true });
        var tmp = SettingsFilePath + ".tmp";
        File.WriteAllText(tmp, json, new UTF8Encoding(false));
        File.Move(tmp, SettingsFilePath, overwrite: true);
    }

    /// <summary>
    /// 读取主窗口布局（尺寸、位置与 WindowState）。
    /// </summary>
    /// <returns>布局记录；缺失项为 null。</returns>
    public static WindowPlacementSettings LoadWindowPlacement()
    {
        var settings = Load();
        string? state = null;
        if (settings.TryGetValue("windowState", out var stateElement) &&
            stateElement.ValueKind == JsonValueKind.String)
        {
            state = stateElement.GetString();
        }
        return new WindowPlacementSettings
        {
            Width = TryReadDouble(settings, "windowWidth"),
            Height = TryReadDouble(settings, "windowHeight"),
            Left = TryReadDouble(settings, "windowLeft"),
            Top = TryReadDouble(settings, "windowTop"),
            State = state,
        };
    }

    /// <summary>
    /// 保存主窗口布局。
    /// </summary>
    /// <param name="width">窗口宽度。</param>
    /// <param name="height">窗口高度。</param>
    /// <param name="left">窗口左边缘。</param>
    /// <param name="top">窗口上边缘。</param>
    /// <param name="windowState">窗口状态字符串。</param>
    public static void SaveWindowPlacement(double width, double height, double left, double top, string windowState)
    {
        var dict = ToMutableDictionary(Load());
        dict["windowWidth"] = width;
        dict["windowHeight"] = height;
        dict["windowLeft"] = left;
        dict["windowTop"] = top;
        dict["windowState"] = windowState;
        Save(dict);
    }

    /// <summary>
    /// 读取 Codex sessions 目录；缺省为当前用户目录下的 .codex/sessions。
    /// </summary>
    /// <returns>Codex sessions 目录路径。</returns>
    public static string LoadCodexSessionsPath()
    {
        var settings = Load();
        if (settings.TryGetValue("codexSessionsPath", out var value) &&
            value.ValueKind == JsonValueKind.String &&
            !string.IsNullOrWhiteSpace(value.GetString()))
        {
            return value.GetString()!;
        }
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".codex",
            "sessions");
    }

    /// <summary>
    /// 保存 Codex sessions 目录。
    /// </summary>
    /// <param name="path">Codex sessions 目录路径。</param>
    public static void SaveCodexSessionsPath(string path)
    {
        var dict = ToMutableDictionary(Load());
        dict["codexSessionsPath"] = path;
        Save(dict);
    }

    /// <summary>
    /// 读取 Cursor tokscale 缓存目录。
    /// </summary>
    /// <returns>cursor-cache 目录路径。</returns>
    public static string LoadCursorCachePath()
    {
        var settings = Load();
        if (settings.TryGetValue("cursorCachePath", out var value) &&
            value.ValueKind == JsonValueKind.String &&
            !string.IsNullOrWhiteSpace(value.GetString()))
        {
            return value.GetString()!;
        }
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".config",
            "tokscale",
            "cursor-cache");
    }

    /// <summary>
    /// 保存 Cursor tokscale 缓存目录。
    /// </summary>
    /// <param name="path">cursor-cache 目录路径。</param>
    public static void SaveCursorCachePath(string path)
    {
        var dict = ToMutableDictionary(Load());
        dict["cursorCachePath"] = path;
        Save(dict);
    }

    /// <summary>
    /// 读取 Antigravity tokscale 缓存目录。
    /// </summary>
    /// <returns>antigravity-cache 目录路径。</returns>
    public static string LoadAntigravityCachePath()
    {
        var settings = Load();
        if (settings.TryGetValue("antigravityCachePath", out var value) &&
            value.ValueKind == JsonValueKind.String &&
            !string.IsNullOrWhiteSpace(value.GetString()))
        {
            return value.GetString()!;
        }
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".config",
            "tokscale",
            "antigravity-cache");
    }

    /// <summary>
    /// 保存 Antigravity tokscale 缓存目录。
    /// </summary>
    /// <param name="path">antigravity-cache 目录路径。</param>
    public static void SaveAntigravityCachePath(string path)
    {
        var dict = ToMutableDictionary(Load());
        dict["antigravityCachePath"] = path;
        Save(dict);
    }

    /// <summary>
    /// 本机 Cursor IDE globalStorage 数据库路径。
    /// </summary>
    /// <returns>state.vscdb 路径。</returns>
    public static string CursorStateDbPath =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "Cursor",
            "User",
            "globalStorage",
            "state.vscdb");

    /// <summary>
    /// 是否可能解析 Cursor 登录态（tokscale 凭证、应用内凭证或本机 state.vscdb）。
    /// </summary>
    /// <returns>存在可用凭证来源时返回 true。</returns>
    public static bool CanResolveCursorAuth() =>
        HasCursorSessionToken() || File.Exists(CursorStateDbPath);

    /// <summary>
    /// 读取已保存的 Cursor Session Token（掩码展示用，不返回明文时可为空）。
    /// </summary>
    /// <returns>是否已配置 token。</returns>
    public static bool HasCursorSessionToken()
    {
        var tokscaleCredPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".config",
            "tokscale",
            "cursor-credentials.json");
        if (File.Exists(tokscaleCredPath) && HasTokenInCredentialsFile(tokscaleCredPath))
            return true;

        var credPath = Path.Combine(AppDataDirectory, "cursor_credentials.json");
        if (!File.Exists(credPath))
            return false;
        return HasTokenInCredentialsFile(credPath);
    }

    private static bool HasTokenInCredentialsFile(string credPath)
    {
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(credPath, Encoding.UTF8));
            var root = doc.RootElement;
            if (root.TryGetProperty("sessionToken", out var direct) &&
                direct.ValueKind == JsonValueKind.String &&
                !string.IsNullOrWhiteSpace(direct.GetString()))
                return true;
            if (root.TryGetProperty("accounts", out var accounts) &&
                root.TryGetProperty("activeAccountId", out var activeId) &&
                accounts.ValueKind == JsonValueKind.Object &&
                activeId.ValueKind == JsonValueKind.String &&
                accounts.TryGetProperty(activeId.GetString()!, out var account) &&
                account.TryGetProperty("sessionToken", out var token) &&
                token.ValueKind == JsonValueKind.String &&
                !string.IsNullOrWhiteSpace(token.GetString()))
                return true;
        }
        catch (JsonException)
        {
        }
        catch (IOException)
        {
        }
        return false;
    }

    private static Dictionary<string, object?> ToMutableDictionary(IReadOnlyDictionary<string, JsonElement> settings)
    {
        var dict = new Dictionary<string, object?>(StringComparer.Ordinal);
        foreach (var pair in settings)
        {
            dict[pair.Key] = pair.Value.ValueKind switch
            {
                JsonValueKind.String => pair.Value.GetString(),
                JsonValueKind.Number => pair.Value.TryGetDouble(out var number) ? number : pair.Value.GetRawText(),
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                JsonValueKind.Null => null,
                _ => JsonSerializer.Deserialize<object>(pair.Value.GetRawText()),
            };
        }
        return dict;
    }

    private static double? TryReadDouble(IReadOnlyDictionary<string, JsonElement> settings, string key)
    {
        if (!settings.TryGetValue(key, out var value))
            return null;
        if (value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number))
            return number;
        if (value.ValueKind == JsonValueKind.String && double.TryParse(value.GetString(), out number))
            return number;
        return null;
    }
}

/// <summary>
/// 主窗口布局偏好（user_settings.json）。
/// </summary>
public sealed class WindowPlacementSettings
{
    /// <summary>窗口宽度。</summary>
    public double? Width { get; init; }

    /// <summary>窗口高度。</summary>
    public double? Height { get; init; }

    /// <summary>窗口左边缘。</summary>
    public double? Left { get; init; }

    /// <summary>窗口上边缘。</summary>
    public double? Top { get; init; }

    /// <summary>窗口状态（Normal / Maximized 等）。</summary>
    public string? State { get; init; }
}
