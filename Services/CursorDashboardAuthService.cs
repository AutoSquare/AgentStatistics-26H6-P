using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace AgentStatistics.Services;

/// <summary>
/// 自动引导 Cursor Dashboard 登录态：IDE Token 构建、浏览器 Cookie 导入与可选系统浏览器唤起。
/// </summary>
public sealed class CursorDashboardAuthService
{
    private const string DashboardUsageUrl = "https://cursor.com/cn/dashboard/usage";

    /// <summary>
    /// 引导 Dashboard 会话，可按调用方要求唤起默认浏览器并等待 Cookie 落盘。
    /// </summary>
    /// <param name="launchBrowser">是否在缺少 Cookie 时自动打开浏览器。</param>
    /// <param name="cancellationToken">取消标记。</param>
    /// <returns>候选 Token 列表（按优先级排序）。</returns>
    public async Task<CursorDashboardBootstrapResult> BootstrapAsync(
        bool launchBrowser = true,
        CancellationToken cancellationToken = default)
    {
        var fromPython = await RunBootstrapScriptAsync(launchBrowser, cancellationToken).ConfigureAwait(false);
        if (fromPython.Candidates.Count > 0)
            return fromPython;

        if (launchBrowser)
        {
            LaunchDashboardBrowser();
            CursorWebSyncLog.Write("auto-launched default browser for cursor.com/cn/dashboard/usage");
        }

        return fromPython;
    }

    /// <summary>
    /// 唤起系统默认浏览器打开 Cursor Dashboard。
    /// </summary>
    public static void LaunchDashboardBrowser()
    {
        try
        {
            Process.Start(new ProcessStartInfo(DashboardUsageUrl)
            {
                UseShellExecute = true,
            });
        }
        catch (Win32Exception ex)
        {
            CursorWebSyncLog.Write($"launch browser failed: {ex.Message}");
        }
        catch (InvalidOperationException ex)
        {
            CursorWebSyncLog.Write($"launch browser failed: {ex.Message}");
        }
    }

    private static async Task<CursorDashboardBootstrapResult> RunBootstrapScriptAsync(
        bool launchBrowser,
        CancellationToken cancellationToken)
    {
        var scriptPath = Path.Combine(AppPaths.PyFolder, "cursor_bootstrap_session.py");
        if (!File.Exists(scriptPath) || !File.Exists(AppPaths.PythonExe))
            return CursorDashboardBootstrapResult.Empty;

        using var proc = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = AppPaths.PythonExe,
                Arguments = $"-u \"{scriptPath}\"",
                WorkingDirectory = AppPaths.PyFolder,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = new UTF8Encoding(false),
                StandardErrorEncoding = new UTF8Encoding(false),
            },
            EnableRaisingEvents = true,
        };
        proc.StartInfo.Environment["PYTHONIOENCODING"] = "utf-8";
        proc.StartInfo.Environment["PYTHONUTF8"] = "1";
        if (!launchBrowser)
            proc.StartInfo.Environment["AS_SKIP_BROWSER_LAUNCH"] = "1";
        proc.Start();
        var stdoutTask = proc.StandardOutput.ReadToEndAsync(cancellationToken);
        try
        {
            await proc.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            TryKill(proc);
            throw;
        }

        if (proc.ExitCode != 0)
            return CursorDashboardBootstrapResult.Empty;

        var stdout = (await stdoutTask.ConfigureAwait(false)).Trim();
        if (string.IsNullOrWhiteSpace(stdout))
            return CursorDashboardBootstrapResult.Empty;

        try
        {
            using var document = JsonDocument.Parse(stdout);
            return CursorDashboardBootstrapResult.Parse(document.RootElement);
        }
        catch (JsonException)
        {
            return CursorDashboardBootstrapResult.Empty;
        }
    }

    private static void TryKill(Process proc)
    {
        try
        {
            if (!proc.HasExited)
                proc.Kill(false);
        }
        catch (InvalidOperationException)
        {
        }
        catch (NotSupportedException)
        {
        }
    }
}

/// <summary>
/// Dashboard 会话引导结果。
/// </summary>
public sealed class CursorDashboardBootstrapResult
{
    /// <summary>空结果。</summary>
    public static CursorDashboardBootstrapResult Empty { get; } = new([], false, null);

    /// <summary>
    /// 初始化引导结果。
    /// </summary>
    /// <param name="candidates">候选 Token。</param>
    /// <param name="launchedBrowser">是否已唤起浏览器。</param>
    /// <param name="primaryToken">首选 Token。</param>
    public CursorDashboardBootstrapResult(
        IReadOnlyList<CursorDashboardTokenCandidate> candidates,
        bool launchedBrowser,
        string? primaryToken)
    {
        Candidates = candidates;
        LaunchedBrowser = launchedBrowser;
        PrimaryToken = primaryToken;
    }

    /// <summary>按优先级排序的候选 Token。</summary>
    public IReadOnlyList<CursorDashboardTokenCandidate> Candidates { get; }

    /// <summary>是否已自动唤起浏览器。</summary>
    public bool LaunchedBrowser { get; }

    /// <summary>首选 Token。</summary>
    public string? PrimaryToken { get; }

    /// <summary>
    /// 解析 Python 引导脚本输出。
    /// </summary>
    /// <param name="root">JSON 根节点。</param>
    /// <returns>引导结果。</returns>
    public static CursorDashboardBootstrapResult Parse(JsonElement root)
    {
        var launched = root.TryGetProperty("launchedBrowser", out var launchedElement)
                       && launchedElement.ValueKind == JsonValueKind.True;
        var primary = root.TryGetProperty("token", out var tokenElement) && tokenElement.ValueKind == JsonValueKind.String
            ? CursorTokenNormalizer.Normalize(tokenElement.GetString())
            : null;
        var candidates = new List<CursorDashboardTokenCandidate>();
        if (root.TryGetProperty("candidates", out var array) && array.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in array.EnumerateArray())
            {
                if (item.ValueKind != JsonValueKind.Object)
                    continue;
                var value = item.TryGetProperty("token", out var tokenValue) && tokenValue.ValueKind == JsonValueKind.String
                    ? CursorTokenNormalizer.Normalize(tokenValue.GetString())
                    : null;
                if (string.IsNullOrWhiteSpace(value))
                    continue;
                var source = item.TryGetProperty("source", out var sourceValue) && sourceValue.ValueKind == JsonValueKind.String
                    ? sourceValue.GetString()
                    : null;
                var email = item.TryGetProperty("email", out var emailValue) && emailValue.ValueKind == JsonValueKind.String
                    ? emailValue.GetString()
                    : null;
                candidates.Add(new CursorDashboardTokenCandidate(
                    value,
                    source,
                    CursorTokenNormalizer.DeriveAccountId(value),
                    email));
            }
        }

        if (candidates.Count == 0 && !string.IsNullOrWhiteSpace(primary))
            candidates.Add(new CursorDashboardTokenCandidate(
                primary,
                "primary",
                CursorTokenNormalizer.DeriveAccountId(primary),
                null));

        return new CursorDashboardBootstrapResult(candidates, launched, primary ?? candidates.FirstOrDefault()?.Token);
    }
}

/// <summary>
/// Dashboard Token 候选项。
/// </summary>
/// <param name="Token">规范化 Token。</param>
/// <param name="Source">来源标识。</param>
/// <param name="AccountId">稳定账号标识。</param>
/// <param name="Email">账号邮箱；不可用时为空。</param>
public sealed record CursorDashboardTokenCandidate(
    string Token,
    string? Source,
    string AccountId,
    string? Email);
