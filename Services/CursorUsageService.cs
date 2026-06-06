using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace AgentStatistics.Services;

/// <summary>
/// 调用 Python Cursor 统计适配器并返回前端可消费的 JSON。
/// </summary>
public sealed class CursorUsageService
{
    /// <summary>
    /// 运行 Cursor 用量统计。
    /// </summary>
    /// <param name="cacheDir">tokscale cursor-cache 目录。</param>
    /// <param name="sessionToken">可选 Cursor Session Token，用于同步。</param>
    /// <param name="sync">是否在统计前尝试云端同步。</param>
    /// <param name="forceSync">为 true 时忽略 cursor-cache 新鲜度，强制拉取云端 CSV。</param>
    /// <param name="skipCloudSync">为 true 时跳过 Python 直连官网 API，仅读取 WebView2 已写入的本地缓存。</param>
    /// <param name="cancellationToken">取消标记。</param>
    /// <returns>统计结果 JSON 文本。</returns>
    public async Task<string> GenerateAsync(
        string cacheDir,
        string? sessionToken = null,
        bool sync = false,
        bool forceSync = false,
        bool skipCloudSync = false,
        CancellationToken cancellationToken = default)
    {
        var scriptPath = Path.Combine(AppPaths.PyFolder, "cursor_usage_stats.py");
        if (!File.Exists(scriptPath))
            throw new FileNotFoundException("未找到 Cursor 统计脚本。", scriptPath);

        if (!File.Exists(AppPaths.PythonExe))
            throw new FileNotFoundException(
                $"Missing portable Python environment at {AppPaths.EnvFolder}. Run setup_python_env.py to create {AppPaths.EnvFolderName}.",
                AppPaths.PythonExe);

        var cachePath = Path.Combine(UserSettingsStore.AppDataDirectory, "cursor_usage_cache.json");
        Directory.CreateDirectory(UserSettingsStore.AppDataDirectory);

        var args = new StringBuilder();
        args.Append($"-u \"{scriptPath}\" --cache-dir \"{cacheDir}\" --cache \"{cachePath}\"");
        if (sync)
            args.Append(" --sync");
        if (forceSync)
            args.Append(" --force-sync");
        if (skipCloudSync)
            args.Append(" --skip-cloud-sync");
        if (!string.IsNullOrWhiteSpace(sessionToken))
            args.Append($" --token \"{sessionToken.Replace("\"", "\\\"")}\"");

        return await RunPythonJsonAsync(args.ToString(), cancellationToken).ConfigureAwait(false);
    }

    private static async Task<string> RunPythonJsonAsync(string arguments, CancellationToken cancellationToken)
    {
        using var proc = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = AppPaths.PythonExe,
                Arguments = arguments,
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
        ApplyPythonProcessEnvironment(proc.StartInfo);
        proc.Start();
        var stdoutTask = proc.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = proc.StandardError.ReadToEndAsync(cancellationToken);
        try
        {
            await proc.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            TryKill(proc);
            throw;
        }

        var stdout = await stdoutTask.ConfigureAwait(false);
        var stderr = await stderrTask.ConfigureAwait(false);
        if (proc.ExitCode != 0)
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(stderr) ? "Cursor 统计脚本执行失败。" : stderr.Trim());
        if (string.IsNullOrWhiteSpace(stdout))
            throw new InvalidOperationException("Cursor 统计脚本没有输出。");

        using var _ = JsonDocument.Parse(stdout);
        return stdout;
    }

    private static void ApplyPythonProcessEnvironment(ProcessStartInfo startInfo)
    {
        startInfo.Environment["PYTHONIOENCODING"] = "utf-8";
        startInfo.Environment["PYTHONUTF8"] = "1";
        startInfo.Environment["AGENTSTATISTICS_ROOT"] = AppPaths.Root;
        AppendNodeJsToPath(startInfo);
    }

    private static void AppendNodeJsToPath(ProcessStartInfo startInfo)
    {
        var additions = new List<string>();
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        if (!string.IsNullOrWhiteSpace(programFiles))
            additions.Add(Path.Combine(programFiles, "nodejs"));
        var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        if (!string.IsNullOrWhiteSpace(appData))
            additions.Add(Path.Combine(appData, "npm"));
        var existing = startInfo.Environment.TryGetValue("PATH", out var pathValue) ? pathValue : Environment.GetEnvironmentVariable("PATH");
        var merged = string.Join(Path.PathSeparator.ToString(), additions.Where(Directory.Exists).Concat(
            (existing ?? string.Empty).Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries)));
        if (!string.IsNullOrWhiteSpace(merged))
            startInfo.Environment["PATH"] = merged;
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
