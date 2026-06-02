using AgentStatistics.Services;
using Microsoft.Extensions.DependencyInjection;

namespace AgentStatistics;

/// <summary>
/// 进程内组合根（memory）：PythonBridge 与会话服务。
/// </summary>
public static partial class CompositionRoot
{
    private static readonly IServiceProvider Services = Build();

    /// <summary>Python 进程桥接服务。</summary>
    public static PythonBridge PythonBridge => Services.GetRequiredService<PythonBridge>();

    /// <summary>计算会话协调器。</summary>
    public static CalculationRunCoordinator CalculationRunCoordinator =>
        Services.GetRequiredService<CalculationRunCoordinator>();

    /// <summary>样本计算服务。</summary>
    public static SampleCalculationService SampleCalculationService =>
        Services.GetRequiredService<SampleCalculationService>();

    /// <summary>Codex 用量统计服务。</summary>
    public static CodexUsageService CodexUsageService =>
        Services.GetRequiredService<CodexUsageService>();

    private static IServiceProvider Build()
    {
        var c = new ServiceCollection();
        c.AddSingleton<CalculationRunCoordinator>();
        c.AddSingleton<PythonBridge>();
        c.AddSingleton<SampleCalculationService>();
        c.AddSingleton<CodexUsageService>();
        return c.BuildServiceProvider();
    }
}
