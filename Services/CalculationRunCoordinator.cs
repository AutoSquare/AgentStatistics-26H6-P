using System.Diagnostics;

namespace AgentStatistics.Services;

/// <summary>
/// 管理计算会话的取消令牌与活跃 Python 子进程，供关窗强制停止使用。
/// </summary>
public sealed class CalculationRunCoordinator
{
    private readonly object _sync = new();
    private readonly List<Process> _activeProcesses = new();
    private CancellationTokenSource? _runCts;
    private bool _cancelExecuted;

    /// <summary>
    /// 用户已确认中止计算并退出时为 true。
    /// </summary>
    public bool IsShuttingDown { get; private set; }

    /// <summary>
    /// 当前计算会话的取消标记。
    /// </summary>
    public CancellationToken Token => _runCts?.Token ?? CancellationToken.None;

    /// <summary>
    /// 开始新的计算会话并返回取消标记。
    /// </summary>
    /// <returns>供 PythonBridge 传递的取消标记。</returns>
    public CancellationToken BeginRun()
    {
        lock (_sync)
        {
            _runCts?.Dispose();
            _runCts = new CancellationTokenSource();
            return _runCts.Token;
        }
    }

    /// <summary>
    /// 结束当前计算会话并释放取消源。
    /// </summary>
    public void EndRun()
    {
        lock (_sync)
        {
            _runCts?.Dispose();
            _runCts = null;
        }
    }

    /// <summary>
    /// 登记由 PythonBridge 启动的活跃子进程。
    /// </summary>
    /// <param name="process">尚未退出的 Python 子进程。</param>
    public void RegisterProcess(Process process)
    {
        ArgumentNullException.ThrowIfNull(process);
        lock (_sync)
        {
            if (process.HasExited)
                return;
            _activeProcesses.Add(process);
        }
    }

    /// <summary>
    /// 从活跃列表移除子进程。
    /// </summary>
    /// <param name="process">待移除的子进程。</param>
    public void UnregisterProcess(Process process)
    {
        ArgumentNullException.ThrowIfNull(process);
        lock (_sync)
        {
            _activeProcesses.Remove(process);
        }
    }

    /// <summary>
    /// 取消当前计算并强制终止已登记子进程。
    /// </summary>
    public void CancelAndKillAll()
    {
        if (_cancelExecuted)
            return;
        _cancelExecuted = true;
        IsShuttingDown = true;
        CancellationTokenSource? cts;
        List<Process> snapshot;
        lock (_sync)
        {
            cts = _runCts;
            snapshot = _activeProcesses.ToList();
        }
        try
        {
            cts?.Cancel();
        }
        catch (ObjectDisposedException)
        {
        }
        foreach (var process in snapshot)
        {
            _ = Task.Run(() => TryKillProcess(process));
        }
        lock (_sync)
        {
            _activeProcesses.Clear();
        }
    }

    private static void TryKillProcess(Process process)
    {
        try
        {
            if (!process.HasExited)
                process.Kill(false);
        }
        catch (InvalidOperationException)
        {
        }
        catch (NotSupportedException)
        {
        }
    }
}
