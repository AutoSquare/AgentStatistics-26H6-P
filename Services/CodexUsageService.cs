using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace AgentStatistics.Services;

/// <summary>
/// 调用 Python Codex 统计适配器并返回前端可消费的 JSON。
/// </summary>
public sealed class CodexUsageService
{
    /// <summary>
    /// 运行 Codex 用量统计。
    /// </summary>
    /// <param name="sessionsPath">Codex sessions 目录。</param>
    /// <param name="cancellationToken">取消标记。</param>
    /// <returns>统计结果 JSON 文本。</returns>
    public async Task<string> GenerateAsync(string sessionsPath, CancellationToken cancellationToken = default)
    {
        var scriptPath = Path.Combine(AppPaths.PyFolder, "codex_usage_stats.py");
        if (!File.Exists(scriptPath))
            throw new FileNotFoundException("未找到 Codex 统计脚本。", scriptPath);

        var pythonExe = ResolvePythonExecutable();
        var cachePath = Path.Combine(UserSettingsStore.AppDataDirectory, "codex_usage_cache.json");
        Directory.CreateDirectory(UserSettingsStore.AppDataDirectory);

        using var proc = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = pythonExe.fileName,
                Arguments = $"{pythonExe.prefixArgs} -u \"{scriptPath}\" --root \"{sessionsPath}\" --cache \"{cachePath}\"",
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
            throw new InvalidOperationException(string.IsNullOrWhiteSpace(stderr) ? "Codex 统计脚本执行失败。" : stderr.Trim());
        if (string.IsNullOrWhiteSpace(stdout))
            throw new InvalidOperationException("Codex 统计脚本没有输出。");

        using var _ = JsonDocument.Parse(stdout);
        return stdout;
    }

    private static (string fileName, string prefixArgs) ResolvePythonExecutable()
    {
        if (File.Exists(AppPaths.PythonExe))
            return (AppPaths.PythonExe, string.Empty);
        return ("py", "-3");
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
