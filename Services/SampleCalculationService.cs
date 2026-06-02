namespace AgentStatistics.Services;

/// <summary>
/// 样本计算编排（memory）：Export → Driver → Merge（快照由 VM 在更新状态栏后写入）。
/// </summary>
public sealed class SampleCalculationService
{
    private readonly PythonBridge _bridge;
    private readonly CalculationRunCoordinator _coordinator;

    /// <summary>
    /// 初始化样本计算服务。
    /// </summary>
    /// <param name="bridge">Python 过桥。</param>
    /// <param name="coordinator">计算协调器。</param>
    public SampleCalculationService(PythonBridge bridge, CalculationRunCoordinator coordinator)
    {
        _bridge = bridge;
        _coordinator = coordinator;
    }

    /// <summary>
    /// 执行样本计算 Driver。
    /// </summary>
    /// <param name="onLogLine">日志行回调。</param>
    /// <param name="cancellationToken">取消标记。</param>
    /// <returns>成功返回 true。</returns>
    public async Task<bool> RunSampleAsync(Action<string>? onLogLine = null, CancellationToken cancellationToken = default)
    {
        ProjectSession.Live.EnsureInitialized();
        var workspace = ProjectSession.Live.ExportWorkspaceForPython();
        try
        {
            var exitCode = await _bridge.RunScriptAsync(
                "CalculateDriver.py",
                workspace,
                new[] { workspace },
                onLogLine,
                cancellationToken).ConfigureAwait(false);
            if (exitCode != 0)
                return false;
            ProjectSession.Live.MergeTablesFromWorkspaceDirectory(workspace);
            return true;
        }
        catch (OperationCanceledException)
        {
            throw;
        }
    }
}
