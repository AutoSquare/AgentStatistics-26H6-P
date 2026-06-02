using System.Diagnostics;
using System.IO;
using System.Text;

namespace AgentStatistics.Services;

/// <summary>
/// 与 Python 运行时之间的过桥：子进程、AS_WORKSPACE 环境变量、标准输入输出。
/// </summary>
public sealed class PythonBridge
{
    private readonly CalculationRunCoordinator _coordinator;

    /// <summary>
    /// 初始化 Python 过桥服务。
    /// </summary>
    /// <param name="coordinator">计算会话协调器。</param>
    public PythonBridge(CalculationRunCoordinator coordinator)
    {
        _coordinator = coordinator ?? throw new ArgumentNullException(nameof(coordinator));
    }

    /// <summary>
    /// 启动 Python 解释器执行指定脚本。
    /// </summary>
    /// <param name="scriptRelativeToPyFolder">相对于 ASPy 的脚本路径。</param>
    /// <param name="workspaceRoot">写入环境变量 AS_WORKSPACE 的工作区根路径。</param>
    /// <param name="stdinLines">写入标准输入的行序列。</param>
    /// <param name="onStdoutLine">标准输出按行回调。</param>
    /// <param name="cancellationToken">取消标记。</param>
    /// <returns>子进程退出码。</returns>
    /// <exception cref="OperationCanceledException">取消且子进程已终止时抛出。</exception>
    public async Task<int> RunScriptAsync(
        string scriptRelativeToPyFolder,
        string workspaceRoot,
        IReadOnlyList<string>? stdinLines = null,
        Action<string>? onStdoutLine = null,
        CancellationToken cancellationToken = default)
    {
        var scriptPath = ResolveAndValidatePaths(scriptRelativeToPyFolder);
        var psi = CreateStartInfo(scriptPath, workspaceRoot);
        using var proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
        WireStdIoHandlers(proc, onStdoutLine);
        proc.Start();
        ProcessJob.Add(proc);
        _coordinator.RegisterProcess(proc);
        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();
        try
        {
            await WriteStdinLinesAsync(proc, stdinLines, cancellationToken).ConfigureAwait(false);
            try
            {
                await proc.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                TryKill(proc);
                throw;
            }
            return proc.ExitCode;
        }
        finally
        {
            _coordinator.UnregisterProcess(proc);
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

    private static string ResolveAndValidatePaths(string scriptRelativeToPyFolder)
    {
        var scriptPath = Path.Combine(AppPaths.PyFolder, scriptRelativeToPyFolder);
        if (!File.Exists(AppPaths.PythonExe))
        {
            var envDir = Path.Combine(AppPaths.Root, AppPaths.EnvFolderName);
            var hint = Directory.Exists(envDir)
                ? $"Python 解释器不存在：{AppPaths.PythonExe}"
                : $"未找到 {AppPaths.EnvFolderName} 目录。请运行 setup_python_env.py 创建虚拟环境后重新生成项目。";
            throw new FileNotFoundException(hint, AppPaths.PythonExe);
        }
        if (!File.Exists(scriptPath))
            throw new FileNotFoundException("未找到脚本。", scriptPath);
        return scriptPath;
    }

    private static ProcessStartInfo CreateStartInfo(string scriptPath, string workspaceRoot)
    {
        var utf8 = new UTF8Encoding(false);
        var psi = new ProcessStartInfo
        {
            FileName = AppPaths.PythonExe,
            Arguments = $"-u \"{scriptPath}\"",
            WorkingDirectory = AppPaths.PyFolder,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardInputEncoding = utf8,
            StandardOutputEncoding = utf8,
            StandardErrorEncoding = utf8,
        };
        psi.Environment["AS_WORKSPACE"] = workspaceRoot;
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUTF8"] = "1";
        return psi;
    }

    private static void WireStdIoHandlers(Process proc, Action<string>? onStdoutLine)
    {
        proc.OutputDataReceived += (_, e) =>
        {
            if (e.Data is null) return;
            onStdoutLine?.Invoke(e.Data);
        };
        proc.ErrorDataReceived += (_, e) =>
        {
            if (e.Data is not null)
                onStdoutLine?.Invoke("<stderr>" + e.Data);
        };
    }

    private static async Task WriteStdinLinesAsync(Process proc, IReadOnlyList<string>? stdinLines, CancellationToken cancellationToken)
    {
        if (stdinLines is { Count: > 0 })
        {
            foreach (var line in stdinLines)
                await proc.StandardInput.WriteLineAsync(line.AsMemory(), cancellationToken).ConfigureAwait(false);
        }
        proc.StandardInput.Close();
    }
}
