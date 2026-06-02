using System.Windows;
using AgentStatistics.Services;
using AgentStatistics.ViewModel;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using System.IO;
using System.Text.Json;
using System.Windows.Threading;

namespace AgentStatistics;

/// <summary>
/// 主窗口：WebView2 仪表盘宿主、Codex 自动刷新与本地设置接线。
/// </summary>
public partial class MainWindow : Window
{
    private readonly MainWindowViewModel _viewModel;
    private readonly CodexUsageService _codexUsageService = CompositionRoot.CodexUsageService;
    private readonly DispatcherTimer _refreshDebounceTimer;
    private FileSystemWatcher? _codexWatcher;
    private CancellationTokenSource? _scanCts;
    private bool _scanRunning;
    private bool _scanQueued;
    private string _codexSessionsPath = string.Empty;

    /// <summary>
    /// 初始化主窗口并注入视图模型。
    /// </summary>
    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnMainWindowLoaded;
        _refreshDebounceTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(900) };
        _refreshDebounceTimer.Tick += OnRefreshDebounceTick;
        _viewModel = CompositionRoot.BuildViewModel();
        DataContext = _viewModel;
        ApplicationBootstrap.OnStartup(_viewModel);
    }

    /// <summary>
    /// 窗口加载完成后恢复尺寸与位置（避免被 XAML 默认值覆盖）。
    /// </summary>
    private async void OnMainWindowLoaded(object sender, RoutedEventArgs e)
    {
        ApplicationBootstrap.ApplyWindowGeometry(this);
        _codexSessionsPath = UserSettingsStore.LoadCodexSessionsPath();
        await InitializeDashboardAsync().ConfigureAwait(true);
        ConfigureWatcher(_codexSessionsPath);
    }

    /// <summary>
    /// 窗口关闭前终止活跃计算子进程并持久化会话。
    /// </summary>
    /// <param name="e">关闭事件参数。</param>
    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        _scanCts?.Cancel();
        _codexWatcher?.Dispose();
        CompositionRoot.CalculationRunCoordinator.CancelAndKillAll();
        ApplicationBootstrap.SaveWindowGeometry(this);
        ProjectSession.Live.PersistOnExit(ProjectSession.BuildUiState(_viewModel.StatusText));
        ProjectSession.Live.CleanupPythonWorkspaceDirectory();
        base.OnClosing(e);
    }

    private async Task InitializeDashboardAsync()
    {
        try
        {
            ConfigureWebView2UserDataFolder();
            await DashboardWebView.EnsureCoreWebView2Async().ConfigureAwait(true);
            DashboardWebView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;
            var indexPath = FindDashboardIndexPath();
            if (indexPath is null)
            {
                DashboardWebView.NavigateToString("<!doctype html><meta charset=\"utf-8\"><body style=\"font-family:Segoe UI;padding:32px;background:#f8fafc;color:#1e3a8a\"><h1>AgentStatistics</h1><p>未找到 WebClient/dist/index.html。请先运行前端构建。</p></body>");
                return;
            }
            DashboardWebView.CoreWebView2.SetVirtualHostNameToFolderMapping(
                "app.agentstatistics.local",
                Path.GetDirectoryName(indexPath)!,
                CoreWebView2HostResourceAccessKind.Allow);
            DashboardWebView.Source = new Uri("https://app.agentstatistics.local/index.html");
        }
        catch (WebView2RuntimeNotFoundException ex)
        {
            MessageBox.Show(
                "未检测到 WebView2 Runtime，请安装 Microsoft Edge WebView2 Runtime 后重新启动。\n\n" + ex.Message,
                "AgentStatistics",
                MessageBoxButton.OK,
                MessageBoxImage.Error);
        }
    }

    private void ConfigureWebView2UserDataFolder()
    {
        if (DashboardWebView.CoreWebView2 is not null)
            return;

        Directory.CreateDirectory(UserSettingsStore.WebView2UserDataDirectory);
        DashboardWebView.CreationProperties ??= new CoreWebView2CreationProperties();
        DashboardWebView.CreationProperties.UserDataFolder = UserSettingsStore.WebView2UserDataDirectory;
    }

    private static string? FindDashboardIndexPath()
    {
        var outputPath = Path.Combine(AppPaths.Root, "WebClient", "dist", "index.html");
        if (File.Exists(outputPath))
            return outputPath;

        var dir = new DirectoryInfo(AppPaths.Root);
        while (dir is not null)
        {
            var sourcePath = Path.Combine(dir.FullName, "WebClient", "dist", "index.html");
            if (File.Exists(sourcePath))
                return sourcePath;
            if (File.Exists(Path.Combine(dir.FullName, "AgentStatistics.csproj")))
                break;
            dir = dir.Parent;
        }
        return null;
    }

    private async void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        try
        {
            using var doc = JsonDocument.Parse(e.WebMessageAsJson);
            var root = doc.RootElement;
            var type = root.TryGetProperty("type", out var typeElement) ? typeElement.GetString() : null;
            switch (type)
            {
                case "ready":
                    PostJson($$"""{"type":"settings","codexRoot":{{JsonSerializer.Serialize(_codexSessionsPath)}}}""");
                    QueueRefresh(TimeSpan.FromMilliseconds(100));
                    break;
                case "refresh":
                    QueueRefresh(TimeSpan.Zero);
                    break;
                case "setCodexRoot":
                    if (root.TryGetProperty("path", out var pathElement))
                    {
                        var path = pathElement.GetString();
                        if (!string.IsNullOrWhiteSpace(path))
                            await ChangeCodexRootAsync(path).ConfigureAwait(true);
                    }
                    break;
            }
        }
        catch (JsonException ex)
        {
            PostError("无法解析前端消息：" + ex.Message);
        }
    }

    private async Task ChangeCodexRootAsync(string path)
    {
        _codexSessionsPath = path.Trim();
        UserSettingsStore.SaveCodexSessionsPath(_codexSessionsPath);
        ConfigureWatcher(_codexSessionsPath);
        PostJson($$"""{"type":"settings","codexRoot":{{JsonSerializer.Serialize(_codexSessionsPath)}}}""");
        await RefreshCodexAsync().ConfigureAwait(true);
    }

    private void ConfigureWatcher(string path)
    {
        _codexWatcher?.Dispose();
        _codexWatcher = null;
        if (!Directory.Exists(path))
        {
            PostJson($$"""{"type":"watcher","active":false,"message":{{JsonSerializer.Serialize("目录不存在，等待手动刷新或修改路径。")}}}""");
            return;
        }

        _codexWatcher = new FileSystemWatcher(path, "*.jsonl")
        {
            IncludeSubdirectories = true,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        _codexWatcher.Changed += OnCodexFilesChanged;
        _codexWatcher.Created += OnCodexFilesChanged;
        _codexWatcher.Deleted += OnCodexFilesChanged;
        _codexWatcher.Renamed += OnCodexFilesChanged;
        PostJson("""{"type":"watcher","active":true,"message":"正在监听 Codex 会话日志。"}""");
    }

    private void OnCodexFilesChanged(object sender, FileSystemEventArgs e)
    {
        Dispatcher.Invoke(() => QueueRefresh(TimeSpan.FromMilliseconds(900)));
    }

    private void QueueRefresh(TimeSpan delay)
    {
        _refreshDebounceTimer.Stop();
        _refreshDebounceTimer.Interval = delay <= TimeSpan.Zero ? TimeSpan.FromMilliseconds(1) : delay;
        _refreshDebounceTimer.Start();
    }

    private async void OnRefreshDebounceTick(object? sender, EventArgs e)
    {
        _refreshDebounceTimer.Stop();
        await RefreshCodexAsync().ConfigureAwait(true);
    }

    private async Task RefreshCodexAsync()
    {
        if (_scanRunning)
        {
            _scanQueued = true;
            PostJson("""{"type":"status","status":"queued","message":"扫描正在进行，已合并下一次刷新。"}""");
            return;
        }

        do
        {
            _scanQueued = false;
            _scanRunning = true;
            _scanCts?.Cancel();
            _scanCts = new CancellationTokenSource();
            _viewModel.StatusText = "正在扫描 Codex 用量...";
            PostJson("""{"type":"status","status":"scanning","message":"正在扫描 Codex 会话日志..."}""");
            try
            {
                var payload = await _codexUsageService.GenerateAsync(_codexSessionsPath, _scanCts.Token).ConfigureAwait(true);
                _viewModel.StatusText = "Codex 用量统计已刷新";
                PostJson($$"""{"type":"codexData","payload":{{payload}}}""");
                PostJson("""{"type":"status","status":"idle","message":"已同步 Codex 用量。"}""");
            }
            catch (OperationCanceledException)
            {
                PostJson("""{"type":"status","status":"cancelled","message":"扫描已取消。"}""");
            }
            catch (Exception ex)
            {
                _viewModel.StatusText = "Codex 用量统计失败";
                PostError(ex.Message);
            }
            finally
            {
                _scanRunning = false;
            }
        } while (_scanQueued);
    }

    private void PostError(string message)
    {
        PostJson($$"""{"type":"status","status":"error","message":{{JsonSerializer.Serialize(message)}}}""");
    }

    private void PostJson(string json)
    {
        if (DashboardWebView.CoreWebView2 is null)
            return;
        DashboardWebView.CoreWebView2.PostWebMessageAsJson(json);
    }
}
