using System.Windows;
using AgentStatistics.Services;
using AgentStatistics.ViewModel;
using Microsoft.Extensions.DependencyInjection;

namespace AgentStatistics;

/// <summary>
/// 主窗口：挂载视图模型并在关闭时协调 Python 子进程与持久化。
/// </summary>
public partial class MainWindow : Window
{
    private readonly MainWindowViewModel _viewModel;

    /// <summary>
    /// 初始化主窗口并注入视图模型。
    /// </summary>
    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnMainWindowLoaded;
        _viewModel = CompositionRoot.BuildViewModel();
        DataContext = _viewModel;
        ApplicationBootstrap.OnStartup(_viewModel);
    }

    /// <summary>
    /// 窗口加载完成后恢复尺寸与位置（避免被 XAML 默认值覆盖）。
    /// </summary>
    private void OnMainWindowLoaded(object sender, RoutedEventArgs e)
    {
        ApplicationBootstrap.ApplyWindowGeometry(this);
    }

    /// <summary>
    /// 窗口关闭前终止活跃计算子进程并持久化会话。
    /// </summary>
    /// <param name="e">关闭事件参数。</param>
    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        if (_viewModel.IsBusy)
        {
            var result = MessageBox.Show(
                "计算正在进行，确定要退出吗？",
                "Agent Statistics",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning);
            if (result != MessageBoxResult.Yes)
            {
                e.Cancel = true;
                return;
            }
        }
        CompositionRoot.CalculationRunCoordinator.CancelAndKillAll();
        ApplicationBootstrap.SaveWindowGeometry(this);
        ProjectSession.Live.PersistOnExit(ProjectSession.BuildUiState(_viewModel.StatusText));
        ProjectSession.Live.CleanupPythonWorkspaceDirectory();
        base.OnClosing(e);
    }
}
