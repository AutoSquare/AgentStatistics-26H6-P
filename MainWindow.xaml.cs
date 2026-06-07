using System.Runtime.InteropServices;
using System.Windows;
using AgentStatistics.Services;
using AgentStatistics.ViewModel;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows.Threading;

namespace AgentStatistics;

/// <summary>
/// 主窗口：WebView2 仪表盘宿主、多 Agent 自动刷新与本地设置接线。
/// </summary>
public partial class MainWindow : Window
{
    private readonly MainWindowViewModel _viewModel;
    private readonly CodexUsageService _codexUsageService = CompositionRoot.CodexUsageService;
    private readonly CursorUsageService _cursorUsageService = CompositionRoot.CursorUsageService;
    private readonly CursorWebSyncService _cursorWebSyncService = CompositionRoot.CursorWebSyncService;
    private readonly AntigravityUsageService _antigravityUsageService = CompositionRoot.AntigravityUsageService;
    private readonly DispatcherTimer _refreshDebounceTimer;
    private readonly DispatcherTimer _dashboardResizeTimer;
    private readonly DispatcherTimer _antigravityAutoSyncTimer;
    private readonly DispatcherTimer _cursorAutoSyncTimer;
    private FileSystemWatcher? _codexWatcher;
    private FileSystemWatcher? _cursorWatcher;
    private FileSystemWatcher? _cursorCliConfigWatcher;
    private FileSystemWatcher? _cursorCliAuthWatcher;
    private FileSystemWatcher? _antigravityWatcher;
    private readonly List<FileSystemWatcher> _antigravityTranscriptWatchers = new();
    private readonly Dictionary<string, CancellationTokenSource> _scanCtsBySource = new(StringComparer.Ordinal);
    private readonly HashSet<string> _pendingRefresh = new(StringComparer.Ordinal);
    private readonly HashSet<string> _runningRefresh = new(StringComparer.Ordinal);
    private string _codexSessionsPath = string.Empty;
    private string _cursorCachePath = string.Empty;
    private string _antigravityCachePath = string.Empty;
    private bool _antigravityForceSync;
    private bool _cursorForceSync;
    private bool _cursorForceFullSync;
    private bool _cursorLoginOpen;
    private bool _cursorSuppressCacheWatcher;
    private bool _cursorWebSyncInProgress;
    private DateTimeOffset _cursorWebSyncBackoffUntil = DateTimeOffset.MinValue;

    /// <summary>
    /// 初始化主窗口并注入视图模型。
    /// </summary>
    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnMainWindowLoaded;
        SizeChanged += OnDashboardHostResized;
        StateChanged += OnDashboardHostStateChanged;
        _refreshDebounceTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(900) };
        _refreshDebounceTimer.Tick += OnRefreshDebounceTick;
        _dashboardResizeTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(160) };
        _dashboardResizeTimer.Tick += OnDashboardResizeTimerTick;
        _antigravityAutoSyncTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(15) };
        _antigravityAutoSyncTimer.Tick += OnAntigravityAutoSyncTimerTick;
        _cursorAutoSyncTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(30) };
        _cursorAutoSyncTimer.Tick += OnCursorAutoSyncTimerTick;
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
        _cursorCachePath = UserSettingsStore.LoadCursorCachePath();
        _antigravityCachePath = UserSettingsStore.LoadAntigravityCachePath();
        await InitializeDashboardAsync().ConfigureAwait(true);
        ConfigureCodexWatcher(_codexSessionsPath);
        ConfigureCursorWatcher(_cursorCachePath);
        ConfigureCursorCliAuthWatchers();
        ConfigureAntigravityWatcher(_antigravityCachePath);
    }

    /// <summary>
    /// 窗口关闭前终止活跃计算子进程并持久化会话。
    /// </summary>
    /// <param name="e">关闭事件参数。</param>
    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        CancelAllScans();
        _codexWatcher?.Dispose();
        _cursorWatcher?.Dispose();
        _cursorCliConfigWatcher?.Dispose();
        _cursorCliAuthWatcher?.Dispose();
        _antigravityWatcher?.Dispose();
        ClearAntigravityTranscriptWatchers();
        _antigravityAutoSyncTimer.Stop();
        _cursorAutoSyncTimer.Stop();
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
            await EnsureCursorSyncWebViewAsync().ConfigureAwait(true);
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
            QueueDashboardResize();
        }
        catch (WebView2RuntimeNotFoundException ex)
        {
            MessageBox.Show(
                "安装包未能自动准备 WebView2 Runtime，请重新运行完整安装包或联系维护人员处理。\n\n" + ex.Message,
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
        var userDataFolder = UserSettingsStore.WebView2UserDataDirectory;
        DashboardWebView.CreationProperties ??= new CoreWebView2CreationProperties();
        DashboardWebView.CreationProperties.UserDataFolder = userDataFolder;
        CursorSyncWebView.CreationProperties ??= new CoreWebView2CreationProperties();
        CursorSyncWebView.CreationProperties.UserDataFolder = userDataFolder;
        CursorLoginWebView.CreationProperties ??= new CoreWebView2CreationProperties();
        CursorLoginWebView.CreationProperties.UserDataFolder = userDataFolder;
    }

    /// <summary>
    /// 初始化用于 Cursor 云端同步的隐藏 WebView2，与仪表盘共享运行时环境。
    /// </summary>
    private async Task EnsureCursorSyncWebViewAsync()
    {
        if (CursorSyncWebView.CoreWebView2 is not null)
            return;
        if (DashboardWebView.CoreWebView2 is null)
            await DashboardWebView.EnsureCoreWebView2Async().ConfigureAwait(true);
        var environment = DashboardWebView.CoreWebView2?.Environment;
        if (environment is null)
            return;
        await CursorSyncWebView.EnsureCoreWebView2Async(environment).ConfigureAwait(true);
    }

    /// <summary>
    /// 初始化用于 Cursor 交互登录的 WebView2，与后台同步共享运行时环境。
    /// </summary>
    private async Task EnsureCursorLoginWebViewAsync()
    {
        if (CursorLoginWebView.CoreWebView2 is not null)
            return;
        if (DashboardWebView.CoreWebView2 is null)
            await DashboardWebView.EnsureCoreWebView2Async().ConfigureAwait(true);
        var environment = DashboardWebView.CoreWebView2?.Environment;
        if (environment is null)
            return;
        await CursorLoginWebView.EnsureCoreWebView2Async(environment).ConfigureAwait(true);
    }

    /// <summary>
    /// 打开 Cursor 官网登录引导：使用系统浏览器完成 SSO，避免内嵌 WebView2 无法登录。
    /// </summary>
    private async Task OpenCursorLoginAsync()
    {
        if (_cursorLoginOpen)
        {
            CursorLoginOverlay.Visibility = Visibility.Visible;
            CursorWebSyncLog.Write("cursor login overlay already open; keeping current login navigation");
            return;
        }

        _cursorLoginOpen = true;
        _cursorAutoSyncTimer.Stop();
        CursorLoginOverlay.Visibility = Visibility.Visible;
        UpdateLayout();
        await EnsureCursorSyncWebViewAsync().ConfigureAwait(true);
        await EnsureCursorLoginWebViewAsync().ConfigureAwait(true);
        if (CursorSyncWebView.CoreWebView2 is not null)
            await CursorWebSyncService.ClearDashboardSessionAsync(CursorSyncWebView.CoreWebView2).ConfigureAwait(true);
        if (CursorLoginWebView.CoreWebView2 is not null)
        {
            await CursorWebSyncService.ClearDashboardSessionAsync(CursorLoginWebView.CoreWebView2).ConfigureAwait(true);
            CursorLoginWebView.CoreWebView2.Navigate(CursorWebSyncService.DashboardSpendingUrl);
        }
        await CursorWebSyncService.MarkWebsiteSessionOfflineAsync(_cursorCachePath).ConfigureAwait(true);
        await CursorWebSyncService.MarkWebsiteSyncOfflineAsync(UserSettingsStore.AppDataDirectory).ConfigureAwait(true);
        PostSettings();
        CursorWebSyncLog.Write("cursor login overlay opened; cleared app webview session and navigated login webview");
        _ = CompleteCursorLoginAsync(showFailureMessage: false);
    }

    private void CursorLoginOpenBrowserButton_Click(object sender, RoutedEventArgs e)
    {
        CursorDashboardAuthService.LaunchDashboardBrowser();
    }

    /// <summary>
    /// 登录浮层打开失败时恢复 UI 与定时器，并提示用户。
    /// </summary>
    /// <param name="message">错误说明。</param>
    private void FailOpenCursorLogin(string message)
    {
        CloseCursorLoginOverlay();
        MessageBox.Show(message, "AgentStatistics", MessageBoxButton.OK, MessageBoxImage.Warning);
    }

    /// <summary>
    /// 关闭 Cursor 登录浮层并恢复后台同步定时器。
    /// </summary>
    private void CloseCursorLoginOverlay()
    {
        CursorLoginOverlay.Visibility = Visibility.Collapsed;
        _cursorLoginOpen = false;
        _cursorAutoSyncTimer.Start();
    }

    private void CursorLoginCancelButton_Click(object sender, RoutedEventArgs e)
    {
        CloseCursorLoginOverlay();
    }

    private async void CursorLoginDoneButton_Click(object sender, RoutedEventArgs e)
    {
        await CompleteCursorLoginAsync(showFailureMessage: true).ConfigureAwait(true);
    }

    private async Task CompleteCursorLoginAsync(bool showFailureMessage)
    {
        var loginWebView = CursorLoginWebView.CoreWebView2;
        _cursorAutoSyncTimer.Stop();
        _viewModel.StatusText = "正在读取浏览器登录态...";
        var synced = false;
        try
        {
            synced = await RunCursorWebSyncOnUiThreadAsync(
                CancellationToken.None,
                webView: loginWebView,
                reuseCurrentNavigation: loginWebView is not null,
                tryBootstrapCandidates: true,
                bootstrapWaitSeconds: 45,
                ignoreBackoff: true,
                allowLoginOverlay: true).ConfigureAwait(true);
        }
        finally
        {
            if (!_cursorLoginOpen)
                _cursorAutoSyncTimer.Start();
        }

        PostSettings();
        if (synced)
        {
            CloseCursorLoginOverlay();
            QueueCursorRefresh(TimeSpan.Zero, sync: false);
        }
        else
        {
            CursorLoginOverlay.Visibility = Visibility.Visible;
            _cursorLoginOpen = true;
            if (showFailureMessage)
            {
                MessageBox.Show(
                    "仍未检测到 Cursor 官网登录态。\n\n请在应用内登录窗口完成 cursor.com 登录，并确认已进入 spending 页面，然后再次点击「完成登录」。",
                    "AgentStatistics",
                    MessageBoxButton.OK,
                    MessageBoxImage.Information);
            }
        }
    }

    /// <summary>
    /// 在 WPF UI 线程执行 Cursor WebView2 官网同步；<see cref="CoreWebView2"/> 成员禁止在后台线程访问。
    /// </summary>
    /// <param name="cancellationToken">取消标记。</param>
    /// <param name="webView">可选指定 WebView2；登录完成后应使用登录浮层实例以读取刚写入的 Cookie。</param>
    /// <param name="reuseCurrentNavigation">是否保留当前 Dashboard 页面（登录浮层完成登录时使用）。</param>
    /// <param name="tryBootstrapCandidates">是否在官网 Cookie 失效时回退 IDE / 浏览器凭据。</param>
    /// <param name="bootstrapWaitSeconds">引导脚本等待浏览器 Cookie 的秒数。</param>
    /// <param name="ignoreBackoff">是否忽略后台同步失败后的退避时间；用户手动完成登录时使用。</param>
    /// <param name="allowLoginOverlay">是否允许登录浮层打开时执行同步；交互登录 WebView 使用。</param>
    /// <returns>同步是否成功写入完整用量快照。</returns>
    private async Task<bool> RunCursorWebSyncOnUiThreadAsync(
        CancellationToken cancellationToken,
        CoreWebView2? webView = null,
        bool reuseCurrentNavigation = false,
        bool tryBootstrapCandidates = false,
        int bootstrapWaitSeconds = 0,
        bool ignoreBackoff = false,
        bool allowLoginOverlay = false)
    {
        if (_cursorLoginOpen && webView is null && !allowLoginOverlay)
        {
            CursorWebSyncLog.Write("cursor web sync skipped: login overlay is open");
            return false;
        }

        if (_cursorWebSyncInProgress)
        {
            CursorWebSyncLog.Write("cursor web sync skipped: already in progress");
            return false;
        }

        if (!ignoreBackoff && DateTimeOffset.UtcNow < _cursorWebSyncBackoffUntil)
        {
            CursorWebSyncLog.Write("cursor web sync skipped: backoff active");
            return false;
        }

        async Task<bool> RunAsync()
        {
            _cursorWebSyncInProgress = true;
            _cursorSuppressCacheWatcher = true;
            try
            {
                CoreWebView2? targetWebView = webView;
                if (targetWebView is null)
                {
                    await EnsureCursorSyncWebViewAsync().ConfigureAwait(true);
                    targetWebView = CursorSyncWebView.CoreWebView2;
                }

                if (targetWebView is null)
                {
                    CursorWebSyncLog.Write("cursor web sync skipped: CoreWebView2 unavailable");
                    return false;
                }

                var ok = await _cursorWebSyncService.TrySyncDashboardAsync(
                    _cursorCachePath,
                    UserSettingsStore.AppDataDirectory,
                    targetWebView,
                    null,
                    cancellationToken,
                    reuseCurrentNavigation,
                    tryBootstrapCandidates,
                    bootstrapWaitSeconds).ConfigureAwait(true);
                if (!ok)
                    _cursorWebSyncBackoffUntil = DateTimeOffset.UtcNow.AddMinutes(3);
                else
                    _cursorWebSyncBackoffUntil = DateTimeOffset.MinValue;
                return ok;
            }
            finally
            {
                _cursorWebSyncInProgress = false;
                _cursorSuppressCacheWatcher = false;
            }
        }

        if (Dispatcher.CheckAccess())
        {
            return await RunAsync().ConfigureAwait(true);
        }

        return await Dispatcher.InvokeAsync(RunAsync).Task.Unwrap().ConfigureAwait(true);
    }

    private void OnDashboardHostResized(object? sender, EventArgs e)
    {
        QueueDashboardResize();
    }

    private void OnDashboardHostStateChanged(object? sender, EventArgs e)
    {
        QueueDashboardResize();
    }

    private void QueueDashboardResize()
    {
        _dashboardResizeTimer.Stop();
        _dashboardResizeTimer.Start();
    }

    private void OnDashboardResizeTimerTick(object? sender, EventArgs e)
    {
        _dashboardResizeTimer.Stop();
        ForceDashboardLayout();
        PostJson("""{"type":"dashboardResize"}""");
    }

    private void ForceDashboardLayout()
    {
        UpdateLayout();
        DashboardWebView.InvalidateMeasure();
        DashboardWebView.InvalidateArrange();
        DashboardWebView.UpdateLayout();
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
                    PostSettings();
                    QueueRefresh("codex", TimeSpan.FromMilliseconds(100));
                    QueueCursorRefresh(TimeSpan.FromMilliseconds(400), sync: false);
                    QueueCursorRefresh(TimeSpan.FromMilliseconds(2000), sync: true, forceFullSync: true);
                    QueueAntigravityRefresh(TimeSpan.FromMilliseconds(200), sync: false);
                    QueueAntigravityRefresh(TimeSpan.FromMilliseconds(1200), sync: true);
                    break;
                case "refresh":
                    QueueRefresh("codex", TimeSpan.Zero);
                    break;
                case "refreshCursor":
                    QueueCursorRefresh(TimeSpan.Zero, sync: true, forceFullSync: true);
                    break;
                case "openCursorLogin":
                    try
                    {
                        await OpenCursorLoginAsync().ConfigureAwait(true);
                    }
                    catch (Exception ex)
                    {
                        FailOpenCursorLogin("打开 Cursor 登录窗口失败：\n\n" + ex.Message);
                    }
                    break;
                case "refreshAntigravity":
                    QueueAntigravityRefresh(TimeSpan.Zero, sync: true);
                    break;
                case "setCodexRoot":
                    if (root.TryGetProperty("path", out var codexPathElement))
                    {
                        var path = codexPathElement.GetString();
                        if (!string.IsNullOrWhiteSpace(path))
                            await ChangeCodexRootAsync(path).ConfigureAwait(true);
                    }
                    break;
                case "setCursorCachePath":
                    if (root.TryGetProperty("path", out var cursorPathElement))
                    {
                        var path = cursorPathElement.GetString();
                        if (!string.IsNullOrWhiteSpace(path))
                            await ChangeCursorCachePathAsync(path).ConfigureAwait(true);
                    }
                    break;
                case "setAntigravityCachePath":
                    if (root.TryGetProperty("path", out var antigravityPathElement))
                    {
                        var path = antigravityPathElement.GetString();
                        if (!string.IsNullOrWhiteSpace(path))
                            await ChangeAntigravityCachePathAsync(path).ConfigureAwait(true);
                    }
                    break;
            }
        }
        catch (JsonException ex)
        {
            PostError("无法解析前端消息：" + ex.Message);
        }
    }

    private void PostSettings()
    {
        var json = new StringBuilder();
        json.Append("{\"type\":\"settings\"");
        json.Append(",\"codexRoot\":").Append(JsonSerializer.Serialize(_codexSessionsPath));
        json.Append(",\"cursorCachePath\":").Append(JsonSerializer.Serialize(_cursorCachePath));
        json.Append(",\"antigravityCachePath\":").Append(JsonSerializer.Serialize(_antigravityCachePath));
        json.Append(",\"cursorAuthAvailable\":").Append(IsCursorWebsiteAuthenticated() ? "true" : "false");
        json.Append(",\"cursorLocalCacheAvailable\":").Append(HasCursorLocalCache() ? "true" : "false");
        json.Append('}');
        PostJson(json.ToString());
    }

    private bool IsCursorWebsiteAuthenticated() =>
        UserSettingsStore.HasCursorCliAuth()
        || (TryReadUsageAccountOnline(out _) && !LastCursorSyncUsedExternalBrowser());

    private static bool LastCursorSyncUsedExternalBrowser()
    {
        var path = Path.Combine(UserSettingsStore.AppDataDirectory, "cursor_usage_sync_status.json");
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
            var root = document.RootElement;
            return root.TryGetProperty("source", out var source)
                   && source.ValueKind == JsonValueKind.String
                   && string.Equals(source.GetString(), "edge-devtools-json", StringComparison.OrdinalIgnoreCase);
        }
        catch (IOException)
        {
            return false;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static bool HasCursorLocalCache()
    {
        var cacheDir = UserSettingsStore.LoadCursorCachePath();
        return File.Exists(Path.Combine(cacheDir, "usage.json"))
            || Directory.EnumerateFiles(cacheDir, "usage*.csv", SearchOption.TopDirectoryOnly).Any();
    }

    /// <summary>
    /// 读取 usage-account.json 中的官网在线标记。
    /// </summary>
    private bool TryReadUsageAccountOnline(out string? accountId)
    {
        accountId = null;
        var path = Path.Combine(_cursorCachePath, "usage-account.json");
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
            var root = document.RootElement;
            if (!root.TryGetProperty("isOnline", out var online) || online.ValueKind != JsonValueKind.True)
                return false;
            if (!root.TryGetProperty("accountId", out var accountElement)
                || accountElement.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(accountElement.GetString()))
            {
                return false;
            }

            accountId = accountElement.GetString();
            return true;
        }
        catch (IOException)
        {
            return false;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private async Task ChangeCodexRootAsync(string path)
    {
        _codexSessionsPath = path.Trim();
        UserSettingsStore.SaveCodexSessionsPath(_codexSessionsPath);
        ConfigureCodexWatcher(_codexSessionsPath);
        PostSettings();
        await RefreshSourceAsync("codex").ConfigureAwait(true);
    }

    private async Task ChangeCursorCachePathAsync(string path)
    {
        _cursorCachePath = path.Trim();
        UserSettingsStore.SaveCursorCachePath(_cursorCachePath);
        ConfigureCursorWatcher(_cursorCachePath);
        PostSettings();
        _cursorForceSync = true;
        _cursorForceFullSync = true;
        await RefreshSourceAsync("cursor").ConfigureAwait(true);
    }

    private async Task ChangeAntigravityCachePathAsync(string path)
    {
        _antigravityCachePath = path.Trim();
        UserSettingsStore.SaveAntigravityCachePath(_antigravityCachePath);
        ConfigureAntigravityWatcher(_antigravityCachePath);
        PostSettings();
        _antigravityForceSync = true;
        await RefreshSourceAsync("antigravity").ConfigureAwait(true);
    }

    private void ConfigureCodexWatcher(string path)
    {
        _codexWatcher?.Dispose();
        _codexWatcher = null;
        if (!Directory.Exists(path))
        {
            PostJson($$"""{"type":"watcher","source":"codex","active":false,"message":{{JsonSerializer.Serialize("目录不存在，等待手动刷新或修改路径。")}}}""");
            return;
        }

        _codexWatcher = new FileSystemWatcher(path, "*.jsonl")
        {
            IncludeSubdirectories = true,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        _codexWatcher.Changed += (_, _) => Dispatcher.Invoke(() => QueueRefresh("codex", TimeSpan.FromMilliseconds(900)));
        _codexWatcher.Created += (_, _) => Dispatcher.Invoke(() => QueueRefresh("codex", TimeSpan.FromMilliseconds(900)));
        _codexWatcher.Deleted += (_, _) => Dispatcher.Invoke(() => QueueRefresh("codex", TimeSpan.FromMilliseconds(900)));
        _codexWatcher.Renamed += (_, _) => Dispatcher.Invoke(() => QueueRefresh("codex", TimeSpan.FromMilliseconds(900)));
        PostJson("""{"type":"watcher","source":"codex","active":true,"message":"正在监听 Codex 会话日志。"}""");
    }

    private void ConfigureAntigravityWatcher(string path)
    {
        _antigravityWatcher?.Dispose();
        _antigravityWatcher = null;
        ClearAntigravityTranscriptWatchers();
        var sessionsPath = Path.Combine(path, "sessions");
        if (!Directory.Exists(sessionsPath))
            Directory.CreateDirectory(sessionsPath);

        _antigravityWatcher = new FileSystemWatcher(sessionsPath, "*.jsonl")
        {
            IncludeSubdirectories = false,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        _antigravityWatcher.Changed += (_, _) => Dispatcher.Invoke(() => QueueAntigravityRefresh(TimeSpan.FromMilliseconds(300), sync: false));
        _antigravityWatcher.Created += (_, _) => Dispatcher.Invoke(() => QueueAntigravityRefresh(TimeSpan.FromMilliseconds(300), sync: false));
        _antigravityWatcher.Deleted += (_, _) => Dispatcher.Invoke(() => QueueAntigravityRefresh(TimeSpan.FromMilliseconds(300), sync: false));
        _antigravityWatcher.Renamed += (_, _) => Dispatcher.Invoke(() => QueueAntigravityRefresh(TimeSpan.FromMilliseconds(300), sync: false));
        ConfigureAntigravityTranscriptWatchers();
        _antigravityAutoSyncTimer.Stop();
        _antigravityAutoSyncTimer.Start();
        PostJson("""{"type":"watcher","source":"antigravity","active":true,"message":"正在监听 Antigravity 用量缓存。"}""");
    }

    private void ConfigureAntigravityTranscriptWatchers()
    {
        foreach (var root in DefaultAntigravityDataRoots())
        {
            var brainPath = Path.Combine(root, "brain");
            if (!Directory.Exists(brainPath))
                continue;

            var watcher = new FileSystemWatcher(brainPath, "*.jsonl")
            {
                IncludeSubdirectories = true,
                NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.CreationTime,
                EnableRaisingEvents = true,
            };
            watcher.Changed += (_, _) => Dispatcher.Invoke(() => QueueAntigravityRefresh(TimeSpan.FromMilliseconds(300), sync: false));
            watcher.Created += (_, _) => Dispatcher.Invoke(() => QueueAntigravityRefresh(TimeSpan.FromMilliseconds(300), sync: false));
            watcher.Deleted += (_, _) => Dispatcher.Invoke(() => QueueAntigravityRefresh(TimeSpan.FromMilliseconds(300), sync: false));
            watcher.Renamed += (_, _) => Dispatcher.Invoke(() => QueueAntigravityRefresh(TimeSpan.FromMilliseconds(300), sync: false));
            _antigravityTranscriptWatchers.Add(watcher);
        }
    }

    private void ClearAntigravityTranscriptWatchers()
    {
        foreach (var watcher in _antigravityTranscriptWatchers)
            watcher.Dispose();
        _antigravityTranscriptWatchers.Clear();
    }

    private static IEnumerable<string> DefaultAntigravityDataRoots()
    {
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (string.IsNullOrWhiteSpace(home))
            yield break;

        var gemini = Path.Combine(home, ".gemini");
        yield return Path.Combine(gemini, "antigravity-cli");
        yield return Path.Combine(gemini, "antigravity");
        yield return Path.Combine(gemini, "antigravity-ide");
        yield return Path.Combine(gemini, "antigravity-backup");
    }

    private void OnCursorAutoSyncTimerTick(object? sender, EventArgs e)
    {
        if (_runningRefresh.Contains("cursor") || _pendingRefresh.Contains("cursor"))
            return;
        QueueCursorRefresh(TimeSpan.FromMilliseconds(1), sync: true, forceFullSync: false);
    }

    private void QueueCursorRefresh(TimeSpan delay, bool sync, bool forceFullSync = false)
    {
        if (sync)
            _cursorForceSync = true;
        if (forceFullSync)
            _cursorForceFullSync = true;
        QueueRefresh("cursor", delay);
    }

    private void ConfigureCursorWatcher(string path)
    {
        _cursorWatcher?.Dispose();
        _cursorWatcher = null;
        if (!Directory.Exists(path))
            Directory.CreateDirectory(path);

        _cursorWatcher = new FileSystemWatcher(path)
        {
            Filter = "*.*",
            IncludeSubdirectories = false,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        void QueueIfArtifact(string? name)
        {
            if (_cursorSuppressCacheWatcher || !IsCursorCacheArtifact(name))
                return;
            QueueCursorRefresh(TimeSpan.FromMilliseconds(300), sync: false);
        }

        _cursorWatcher.Changed += (_, e) => Dispatcher.Invoke(() => QueueIfArtifact(e.Name));
        _cursorWatcher.Created += (_, e) => Dispatcher.Invoke(() => QueueIfArtifact(e.Name));
        _cursorWatcher.Deleted += (_, e) => Dispatcher.Invoke(() => QueueIfArtifact(e.Name));
        _cursorWatcher.Renamed += (_, e) => Dispatcher.Invoke(() =>
        {
            QueueIfArtifact(e.Name);
            QueueIfArtifact(e.OldName);
        });
        _cursorAutoSyncTimer.Stop();
        _cursorAutoSyncTimer.Start();
        PostJson("""{"type":"watcher","source":"cursor","active":true,"message":"正在监听 Cursor 用量缓存。"}""");
    }

    private void ConfigureCursorCliAuthWatchers()
    {
        _cursorCliConfigWatcher?.Dispose();
        _cursorCliAuthWatcher?.Dispose();
        _cursorCliConfigWatcher = CreateCursorAuthWatcher(UserSettingsStore.CursorCliConfigPath);
        _cursorCliAuthWatcher = CreateCursorAuthWatcher(UserSettingsStore.CursorCliAuthPath);
    }

    private FileSystemWatcher? CreateCursorAuthWatcher(string filePath)
    {
        var directory = Path.GetDirectoryName(filePath);
        var fileName = Path.GetFileName(filePath);
        if (string.IsNullOrWhiteSpace(directory) || string.IsNullOrWhiteSpace(fileName))
            return null;
        Directory.CreateDirectory(directory);
        var watcher = new FileSystemWatcher(directory, fileName)
        {
            IncludeSubdirectories = false,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        void QueueSync() => Dispatcher.Invoke(() =>
        {
            _cursorWebSyncBackoffUntil = DateTimeOffset.MinValue;
            QueueCursorRefresh(TimeSpan.FromMilliseconds(500), sync: true, forceFullSync: true);
        });
        watcher.Changed += (_, _) => QueueSync();
        watcher.Created += (_, _) => QueueSync();
        watcher.Deleted += (_, _) => QueueSync();
        watcher.Renamed += (_, _) => QueueSync();
        return watcher;
    }

    private void OnAntigravityAutoSyncTimerTick(object? sender, EventArgs e)
    {
        if (_runningRefresh.Count > 0 || _pendingRefresh.Contains("antigravity"))
            return;
        QueueAntigravityRefresh(TimeSpan.FromMilliseconds(1), sync: true);
    }

    private void QueueAntigravityRefresh(TimeSpan delay, bool sync)
    {
        if (sync)
            _antigravityForceSync = true;
        QueueRefresh("antigravity", delay);
    }

    private void ConfigureCsvWatcher(ref FileSystemWatcher? watcher, string path, string source)
    {
        watcher?.Dispose();
        watcher = null;
        if (!Directory.Exists(path))
        {
            Directory.CreateDirectory(path);
        }

        watcher = new FileSystemWatcher(path, "*.csv")
        {
            IncludeSubdirectories = false,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        watcher.Changed += (_, _) => Dispatcher.Invoke(() => QueueRefresh(source, TimeSpan.FromMilliseconds(900)));
        watcher.Created += (_, _) => Dispatcher.Invoke(() => QueueRefresh(source, TimeSpan.FromMilliseconds(900)));
        watcher.Deleted += (_, _) => Dispatcher.Invoke(() => QueueRefresh(source, TimeSpan.FromMilliseconds(900)));
        watcher.Renamed += (_, _) => Dispatcher.Invoke(() => QueueRefresh(source, TimeSpan.FromMilliseconds(900)));
        PostJson($$"""{"type":"watcher","source":{{JsonSerializer.Serialize(source)}},"active":true,"message":{{JsonSerializer.Serialize($"正在监听 {source} CSV 缓存。")}}}""");
    }

    private void QueueRefresh(string source, TimeSpan delay)
    {
        _pendingRefresh.Add(source);
        _refreshDebounceTimer.Stop();
        _refreshDebounceTimer.Interval = delay <= TimeSpan.Zero ? TimeSpan.FromMilliseconds(1) : delay;
        _refreshDebounceTimer.Start();
    }

    private async void OnRefreshDebounceTick(object? sender, EventArgs e)
    {
        _refreshDebounceTimer.Stop();
        var targets = _pendingRefresh.ToArray();
        _pendingRefresh.Clear();
        await Task.WhenAll(targets.Select(RefreshSourceAsync)).ConfigureAwait(true);
    }

    /// <summary>
    /// 取消指定 Agent 正在进行的扫描；不影响其他 Agent。
    /// </summary>
    /// <param name="source">Agent 标识：codex、cursor、antigravity。</param>
    private void CancelScan(string source)
    {
        if (!_scanCtsBySource.TryGetValue(source, out var cts))
            return;
        cts.Cancel();
        cts.Dispose();
        _scanCtsBySource.Remove(source);
    }

    /// <summary>
    /// 取消全部 Agent 扫描，用于窗口关闭。
    /// </summary>
    private void CancelAllScans()
    {
        foreach (var cts in _scanCtsBySource.Values)
            cts.Cancel();
        foreach (var cts in _scanCtsBySource.Values)
            cts.Dispose();
        _scanCtsBySource.Clear();
    }

    private async Task RefreshSourceAsync(string source)
    {
        if (_runningRefresh.Contains(source))
        {
            _pendingRefresh.Add(source);
            PostJson($$"""{"type":"status","source":{{JsonSerializer.Serialize(source)}},"status":"queued","message":"扫描正在进行，已合并下一次刷新。"}""");
            return;
        }

        _runningRefresh.Add(source);
        CancelScan(source);
        var scanCts = new CancellationTokenSource();
        _scanCtsBySource[source] = scanCts;
        try
        {
            switch (source)
            {
                case "codex":
                    _viewModel.StatusText = "正在扫描 Codex 用量...";
                    PostJson("""{"type":"status","source":"codex","status":"scanning","message":"正在扫描 Codex 会话日志..."}""");
                    var codexPayload = await _codexUsageService.GenerateAsync(_codexSessionsPath, scanCts.Token).ConfigureAwait(true);
                    _viewModel.StatusText = "Codex 用量统计已刷新";
                    PostJson($$"""{"type":"codexData","payload":{{codexPayload}}}""");
                    PostJson("""{"type":"status","source":"codex","status":"idle","message":"已同步 Codex 用量。"}""");
                    break;
                case "cursor":
                    var cursorSync = _cursorForceSync;
                    var cursorForceFullSync = _cursorForceFullSync;
                    _cursorForceSync = false;
                    _cursorForceFullSync = false;
                    _viewModel.StatusText = "正在扫描 Cursor 用量...";
                    PostJson("""{"type":"status","source":"cursor","status":"scanning","message":"正在扫描 Cursor 用量..."}""");
                    var localPayload = await _cursorUsageService.GenerateAsync(
                        _cursorCachePath,
                        sync: false,
                        forceSync: false,
                        skipCloudSync: true,
                        cancellationToken: scanCts.Token).ConfigureAwait(true);
                    PostJson($$"""{"type":"cursorData","payload":{{localPayload}}}""");

                    if (cursorSync)
                        PostJson("""{"type":"status","source":"cursor","status":"scanning","message":"已加载本地用量，正在通过 Cursor CLI 同步..."}""");

                    var cursorPayload = await _cursorUsageService.GenerateAsync(
                        _cursorCachePath,
                        sync: cursorSync,
                        forceSync: cursorForceFullSync,
                        skipCloudSync: !cursorSync,
                        cancellationToken: scanCts.Token).ConfigureAwait(true);
                    var cursorStatusMessage = BuildCursorStatusMessage(cursorPayload);
                    _viewModel.StatusText = "Cursor 用量统计已刷新";
                    PostJson($$"""{"type":"cursorData","payload":{{cursorPayload}}}""");
                    PostSettings();
                    PostJson($$"""{"type":"status","source":"cursor","status":"idle","message":{{JsonSerializer.Serialize(cursorStatusMessage)}}}""");
                    break;
                case "antigravity":
                    var antigravitySync = _antigravityForceSync;
                    _antigravityForceSync = false;
                    _viewModel.StatusText = "正在扫描 Antigravity 用量...";
                    PostJson("""{"type":"status","source":"antigravity","status":"scanning","message":"正在扫描 Antigravity 用量..."}""");
                    var antigravityPayload = await _antigravityUsageService.GenerateAsync(_antigravityCachePath, sync: antigravitySync, scanCts.Token).ConfigureAwait(true);
                    var antigravityStatusMessage = BuildAntigravityStatusMessage(antigravityPayload);
                    _viewModel.StatusText = "Antigravity 用量统计已刷新";
                    PostJson($$"""{"type":"antigravityData","payload":{{antigravityPayload}}}""");
                    PostJson($$"""{"type":"status","source":"antigravity","status":"idle","message":{{JsonSerializer.Serialize(antigravityStatusMessage)}}}""");
                    break;
            }
        }
        catch (OperationCanceledException)
        {
            PostJson($$"""{"type":"status","source":{{JsonSerializer.Serialize(source)}},"status":"cancelled","message":"扫描已取消。"}""");
        }
        catch (Exception ex)
        {
            _viewModel.StatusText = $"{source} 用量统计失败";
            PostError(ex.Message, source);
        }
        finally
        {
            _runningRefresh.Remove(source);
            if (_scanCtsBySource.TryGetValue(source, out var activeCts) && ReferenceEquals(activeCts, scanCts))
            {
                _scanCtsBySource.Remove(source);
                scanCts.Dispose();
            }
            if (_pendingRefresh.Count > 0)
            {
                _refreshDebounceTimer.Stop();
                _refreshDebounceTimer.Interval = TimeSpan.FromMilliseconds(50);
                _refreshDebounceTimer.Start();
            }
        }
    }

    private static bool IsCursorCacheArtifact(string? fileName)
    {
        if (string.IsNullOrWhiteSpace(fileName))
            return false;
        return fileName.Equals("usage.json", StringComparison.OrdinalIgnoreCase)
            || fileName.StartsWith("usage", StringComparison.OrdinalIgnoreCase)
                && fileName.EndsWith(".csv", StringComparison.OrdinalIgnoreCase);
    }

    private static string BuildAntigravityStatusMessage(string payloadJson)
    {
        try
        {
            using var doc = JsonDocument.Parse(payloadJson);
            var root = doc.RootElement;
            var dataStatus = root.TryGetProperty("dataStatus", out var statusElement) ? statusElement.GetString() : null;
            var recordCount = 0;
            if (root.TryGetProperty("records", out var recordsElement) && recordsElement.ValueKind == JsonValueKind.Array)
                recordCount = recordsElement.GetArrayLength();
            if (string.Equals(dataStatus, "ok", StringComparison.Ordinal) && recordCount > 0)
                return "已同步 Antigravity 用量。";
            if (root.TryGetProperty("sync", out var syncElement) && syncElement.ValueKind == JsonValueKind.Object &&
                syncElement.TryGetProperty("error", out var errorElement) && errorElement.ValueKind == JsonValueKind.String)
            {
                var error = errorElement.GetString();
                if (!string.IsNullOrWhiteSpace(error))
                    return error;
            }
            if (string.Equals(dataStatus, "empty", StringComparison.Ordinal))
                return "暂无 Antigravity 用量。";
            return "Antigravity 用量统计已刷新。";
        }
        catch (JsonException)
        {
            return "Antigravity 用量统计已刷新。";
        }
    }

    private static string BuildCursorStatusMessage(string payloadJson)
    {
        try
        {
            using var doc = JsonDocument.Parse(payloadJson);
            var root = doc.RootElement;
            var dataStatus = root.TryGetProperty("dataStatus", out var statusElement) ? statusElement.GetString() : null;
            var recordCount = 0;
            if (root.TryGetProperty("records", out var recordsElement) && recordsElement.ValueKind == JsonValueKind.Array)
                recordCount = recordsElement.GetArrayLength();
            if (root.TryGetProperty("accounts", out var accountsElement) && accountsElement.ValueKind == JsonValueKind.Array)
            {
                foreach (var account in accountsElement.EnumerateArray())
                {
                    var isCurrent = account.TryGetProperty("isCurrent", out var currentElement)
                                    && currentElement.ValueKind == JsonValueKind.True;
                    var syncStatus = account.TryGetProperty("syncStatus", out var accountStatus)
                        ? accountStatus.GetString()
                        : null;
                    if (!isCurrent || string.Equals(syncStatus, "ok", StringComparison.Ordinal))
                        continue;
                    if (account.TryGetProperty("syncMessage", out var messageElement)
                        && messageElement.ValueKind == JsonValueKind.String
                        && !string.IsNullOrWhiteSpace(messageElement.GetString()))
                    {
                        return messageElement.GetString()!;
                    }
                    return "Cursor 官网同步不完整，已保留上一次完整用量快照。";
                }
            }
            if (string.Equals(dataStatus, "ok", StringComparison.Ordinal) && recordCount > 0)
                return "已同步 Cursor 用量。";
            if (root.TryGetProperty("sync", out var syncElement) && syncElement.ValueKind == JsonValueKind.Object &&
                syncElement.TryGetProperty("error", out var errorElement) && errorElement.ValueKind == JsonValueKind.String)
            {
                var error = errorElement.GetString();
                if (!string.IsNullOrWhiteSpace(error))
                    return error;
            }
            if (string.Equals(dataStatus, "empty", StringComparison.Ordinal))
                return "暂无 Cursor 用量。";
            return "Cursor 用量统计已刷新。";
        }
        catch (JsonException)
        {
            return "Cursor 用量统计已刷新。";
        }
    }

    private void PostError(string message, string? source = null)
    {
        if (string.IsNullOrWhiteSpace(source))
            PostJson($$"""{"type":"status","status":"error","message":{{JsonSerializer.Serialize(message)}}}""");
        else
            PostJson($$"""{"type":"status","source":{{JsonSerializer.Serialize(source)}},"status":"error","message":{{JsonSerializer.Serialize(message)}}}""");
    }

    /// <summary>
    /// 向前端 WebView 投递 JSON 消息；可在后台线程调用，内部会切回 UI 线程。
    /// </summary>
    /// <param name="json">已序列化的 JSON 文本。</param>
    private void PostJson(string json)
    {
        if (Dispatcher.CheckAccess())
        {
            PostJsonCore(json);
            return;
        }

        Dispatcher.BeginInvoke(PostJsonCore, json);
    }

    private void PostJsonCore(string json)
    {
        if (DashboardWebView.CoreWebView2 is null)
            return;
        DashboardWebView.CoreWebView2.PostWebMessageAsJson(json);
    }
}
