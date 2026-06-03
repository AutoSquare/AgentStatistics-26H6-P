using System.Diagnostics;
using System.IO;
using System.Text;

namespace AgentStatistics.Services;

/// <summary>
/// Bridge between WPF and Python: child process, workspace environment, and
/// redirected standard streams.
/// </summary>
public sealed class PythonBridge
{
    private readonly CalculationRunCoordinator _coordinator;

    public PythonBridge(CalculationRunCoordinator coordinator)
    {
        _coordinator = coordinator ?? throw new ArgumentNullException(nameof(coordinator));
    }

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
            var hint = Directory.Exists(AppPaths.EnvFolder)
                ? $"Development Python venv exists, but no Python executable was found: {AppPaths.PythonExe}"
                : $"Missing portable Python environment at {AppPaths.EnvFolder}. Run setup_python_env.py to create {AppPaths.EnvFolderName}.";
            throw new FileNotFoundException(hint, AppPaths.PythonExe);
        }
        if (!File.Exists(scriptPath))
            throw new FileNotFoundException("Python script was not found.", scriptPath);
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
