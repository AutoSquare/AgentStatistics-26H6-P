using System.IO;
using System.Windows;
using AgentStatistics.ViewModel;

namespace AgentStatistics.Services;

/// <summary>
/// memory 模式应用启动与窗口偏好恢复。
/// </summary>
public static class ApplicationBootstrap
{
    /// <summary>
    /// 启动时加载会话快照并更新视图模型状态。
    /// </summary>
    /// <param name="viewModel">主窗口视图模型。</param>
    public static void OnStartup(MainWindowViewModel viewModel)
    {
        var hadSnapshot = File.Exists(SessionSnapshotStore.SnapshotFilePath);
        ProjectSession.Live.EnsureInitialized();
        viewModel.RefreshSessionStatus(hadSnapshot, ProjectSession.Live.SavedStatusText);
    }

    /// <summary>
    /// 从 user_settings 恢复窗口尺寸、位置与 WindowState（须在 Loaded 后调用）。
    /// </summary>
    /// <param name="window">主窗口。</param>
    public static void ApplyWindowGeometry(Window window)
    {
        var placement = UserSettingsStore.LoadWindowPlacement();
        if (placement.Width is > 200 && placement.Height is > 150)
        {
            window.Width = Math.Max(placement.Width.Value, window.MinWidth);
            window.Height = Math.Max(placement.Height.Value, window.MinHeight);
        }
        if (placement.Left.HasValue && placement.Top.HasValue)
        {
            window.WindowStartupLocation = WindowStartupLocation.Manual;
            window.Left = placement.Left.Value;
            window.Top = placement.Top.Value;
        }
        if (!string.IsNullOrEmpty(placement.State) &&
            Enum.TryParse<WindowState>(placement.State, out var state))
        {
            window.WindowState = state;
        }
    }

    /// <summary>
    /// 保存窗口布局到 user_settings（最大化时使用 RestoreBounds）。
    /// </summary>
    /// <param name="window">主窗口。</param>
    public static void SaveWindowGeometry(Window window)
    {
        var bounds = window.WindowState == WindowState.Normal
            ? new Rect(window.Left, window.Top, window.Width, window.Height)
            : window.RestoreBounds;
        UserSettingsStore.SaveWindowPlacement(
            Math.Max(bounds.Width, window.MinWidth),
            Math.Max(bounds.Height, window.MinHeight),
            bounds.Left,
            bounds.Top,
            window.WindowState.ToString());
    }
}
