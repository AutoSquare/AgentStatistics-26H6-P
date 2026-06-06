using System.Diagnostics;
using System.IO;
using System.Text;

namespace AgentStatistics.Services;

/// <summary>
/// 通过便携 Python 解析 Cursor 登录态，供 WebView2 云端同步注入 Cookie。
/// </summary>
public sealed class CursorSessionResolver
{
    /// <summary>
    /// 解析当前可用的 Cursor Session Token。
    /// </summary>
    /// <param name="cancellationToken">取消标记。</param>
    /// <returns>规范化 Token；无法解析时返回 null。</returns>
    public async Task<string?> ResolveSessionTokenAsync(CancellationToken cancellationToken = default)
    {
        var scriptPath = Path.Combine(AppPaths.PyFolder, "cursor_resolve_token.py");
        if (!File.Exists(scriptPath) || !File.Exists(AppPaths.PythonExe))
            return null;

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
            return null;

        var stdout = (await stdoutTask.ConfigureAwait(false)).Trim();
        return CursorTokenNormalizer.Normalize(stdout);
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
