using System.Windows;
using CommunityToolkit.Mvvm.Input;
using AgentStatistics.Model.Domain;
using AgentStatistics.Services;

namespace AgentStatistics.ViewModel;

/// <summary>
/// 主窗口视图模型（memory）：状态文本与样本计算，无工程文件菜单。
/// </summary>
public sealed class MainWindowViewModel : ViewModelBase
{
    private static bool _snapshotNoticeShown;
    private readonly SampleCalculationService _calculationService;
    private readonly CalculationRunCoordinator _coordinator;
    private string _statusText = "就绪";
    private bool _isBusy;

    /// <summary>
    /// 初始化主窗口视图模型。
    /// </summary>
    /// <param name="calculationService">样本计算服务。</param>
    /// <param name="coordinator">计算协调器。</param>
    public MainWindowViewModel(SampleCalculationService calculationService, CalculationRunCoordinator coordinator)
    {
        _calculationService = calculationService;
        _coordinator = coordinator;
        RunSampleCommand = new AsyncRelayCommand(RunSampleAsync, () => !IsBusy);
    }

    /// <summary>状态栏文本。</summary>
    public string StatusText
    {
        get => _statusText;
        set => SetProperty(ref _statusText, value);
    }

    /// <summary>是否正在计算。</summary>
    public bool IsBusy
    {
        get => _isBusy;
        private set
        {
            if (SetProperty(ref _isBusy, value))
                RunSampleCommand.NotifyCanExecuteChanged();
        }
    }

    /// <summary>运行样本计算命令。</summary>
    public AsyncRelayCommand RunSampleCommand { get; }

    /// <summary>
    /// 刷新会话状态摘要（启动时调用）。
    /// </summary>
    /// <param name="restoredFromSnapshot">启动前是否存在快照文件。</param>
    /// <param name="savedStatusText">快照 uiState 中保存的状态栏文本。</param>
    public void RefreshSessionStatus(bool restoredFromSnapshot, string? savedStatusText = null)
    {
        if (restoredFromSnapshot && !string.IsNullOrWhiteSpace(savedStatusText))
        {
            StatusText = savedStatusText;
            return;
        }
        var doc = ProjectSession.Live.Document;
        var tableCount = doc.DataTables.Count + doc.MaterialTables.Count;
        var hasResult = doc.DataTables.ContainsKey(ProjectFileFormat.SampleResultTable);
        var snapPath = SessionSnapshotStore.SnapshotFilePath;
        if (restoredFromSnapshot)
        {
            StatusText = hasResult
                ? $"已恢复上次会话（含 {ProjectFileFormat.SampleResultTable}，共 {tableCount} 张表）\n快照：{snapPath}"
                : $"已恢复上次会话（{tableCount} 张表）\n快照：{snapPath}";
        }
        else
        {
            StatusText =
                $"新会话（memory 模式，无另存为菜单；计算后自动写入 AppData 快照）\n共 {tableCount} 张表";
        }
    }

    private async Task RunSampleAsync()
    {
        IsBusy = true;
        StatusText = "计算中…";
        var token = _coordinator.BeginRun();
        try
        {
            var ok = await _calculationService.RunSampleAsync(
                line => StatusText = line.StartsWith("<log>") ? line[5..] : line,
                token).ConfigureAwait(true);
            if (ok)
            {
                var snapPath = SessionSnapshotStore.SnapshotFilePath;
                var hasResult = ProjectSession.Live.Document.DataTables.ContainsKey(ProjectFileFormat.SampleResultTable);
                StatusText = hasResult
                    ? $"样本计算完成，已写入会话快照（含 {ProjectFileFormat.SampleResultTable}）\n{snapPath}"
                    : $"样本计算完成，已写入会话快照\n{snapPath}";
                ProjectSession.Live.SaveSnapshot(ProjectSession.BuildUiState(StatusText));
                MaybeShowSnapshotNoticeOnce(snapPath);
            }
            else
            {
                StatusText = "样本计算失败";
            }
        }
        catch (OperationCanceledException)
        {
            StatusText = "已取消";
        }
        finally
        {
            _coordinator.EndRun();
            IsBusy = false;
        }
    }

    private static void MaybeShowSnapshotNoticeOnce(string snapPath)
    {
        if (_snapshotNoticeShown)
            return;
        _snapshotNoticeShown = true;
        MessageBox.Show(
            $"会话数据已保存至：\n{snapPath}\n\n下次启动将自动恢复（memory 模式，无工程文件菜单）。",
            "AgentStatistics",
            MessageBoxButton.OK,
            MessageBoxImage.Information);
    }
}
