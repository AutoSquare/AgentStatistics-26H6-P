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
    private readonly AntigravityUsageService _antigravityUsageService = CompositionRoot.AntigravityUsageService;
    private readonly DispatcherTimer _refreshDebounceTimer;
    private readonly DispatcherTimer _dashboardResizeTimer;
    private FileSystemWatcher? _codexWatcher;
    private FileSystemWatcher? _cursorWatcher;
    private FileSystemWatcher? _antigravityWatcher;
    private CancellationTokenSource? _scanCts;
    private readonly HashSet<string> _pendingRefresh = new(StringComparer.Ordinal);
    private readonly HashSet<string> _runningRefresh = new(StringComparer.Ordinal);
    private string _codexSessionsPath = string.Empty;
    private string _cursorCachePath = string.Empty;
    private string _antigravityCachePath = string.Empty;

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
        ConfigureCsvWatcher(ref _cursorWatcher, _cursorCachePath, "cursor");
        ConfigureAntigravityWatcher(_antigravityCachePath);
    }

    /// <summary>
    /// 窗口关闭前终止活跃计算子进程并持久化会话。
    /// </summary>
    /// <param name="e">关闭事件参数。</param>
    protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
    {
        _scanCts?.Cancel();
        _codexWatcher?.Dispose();
        _cursorWatcher?.Dispose();
        _antigravityWatcher?.Dispose();
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
        DashboardWebView.CreationProperties ??= new CoreWebView2CreationProperties();
        DashboardWebView.CreationProperties.UserDataFolder = UserSettingsStore.WebView2UserDataDirectory;
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
                    QueueRefresh("cursor", TimeSpan.FromMilliseconds(150));
                    QueueRefresh("antigravity", TimeSpan.FromMilliseconds(200));
                    break;
                case "refresh":
                    QueueRefresh("codex", TimeSpan.Zero);
                    break;
                case "refreshCursor":
                    QueueRefresh("cursor", TimeSpan.Zero);
                    break;
                case "refreshAntigravity":
                    QueueRefresh("antigravity", TimeSpan.Zero);
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
                case "setCursorToken":
                    if (root.TryGetProperty("token", out var tokenElement))
                    {
                        var token = tokenElement.GetString();
                        if (!string.IsNullOrWhiteSpace(token))
                        {
                            await SaveCursorTokenAsync(token).ConfigureAwait(true);
                            QueueRefresh("cursor", TimeSpan.Zero);
                        }
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
        json.Append(",\"cursorAuthAvailable\":").Append(UserSettingsStore.CanResolveCursorAuth() ? "true" : "false");
        json.Append('}');
        PostJson(json.ToString());
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
        ConfigureCsvWatcher(ref _cursorWatcher, _cursorCachePath, "cursor");
        PostSettings();
        await RefreshSourceAsync("cursor").ConfigureAwait(true);
    }

    private async Task ChangeAntigravityCachePathAsync(string path)
    {
        _antigravityCachePath = path.Trim();
        UserSettingsStore.SaveAntigravityCachePath(_antigravityCachePath);
        ConfigureAntigravityWatcher(_antigravityCachePath);
        PostSettings();
        await RefreshSourceAsync("antigravity").ConfigureAwait(true);
    }

    private async Task SaveCursorTokenAsync(string token)
    {
        var normalized = CursorTokenNormalizer.Normalize(token);
        if (string.IsNullOrWhiteSpace(normalized))
        {
            PostError("无效的 Cursor Session Token。请粘贴 WorkosCursorSessionToken Cookie 的值，而非名称。", "cursor");
            return;
        }
        var credPath = Path.Combine(UserSettingsStore.AppDataDirectory, "cursor_credentials.json");
        Directory.CreateDirectory(UserSettingsStore.AppDataDirectory);
        var accountId = CursorTokenNormalizer.DeriveAccountId(normalized);
        var payload = new
        {
            version = 1,
            activeAccountId = accountId,
            accounts = new Dictionary<string, object>
            {
                [accountId] = new
                {
                    sessionToken = normalized,
                    userId = accountId,
                    createdAt = DateTime.UtcNow.ToString("o"),
                    expiresAt = (string?)null,
                    label = (string?)null,
                },
            },
        };
        var json = JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true });
        var tmp = credPath + ".tmp";
        await File.WriteAllTextAsync(tmp, json, Encoding.UTF8).ConfigureAwait(true);
        File.Move(tmp, credPath, overwrite: true);
        PostSettings();
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
        var sessionsPath = Path.Combine(path, "sessions");
        if (!Directory.Exists(sessionsPath))
            Directory.CreateDirectory(sessionsPath);

        _antigravityWatcher = new FileSystemWatcher(sessionsPath, "*.jsonl")
        {
            IncludeSubdirectories = false,
            NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        _antigravityWatcher.Changed += (_, _) => Dispatcher.Invoke(() => QueueRefresh("antigravity", TimeSpan.FromMilliseconds(900)));
        _antigravityWatcher.Created += (_, _) => Dispatcher.Invoke(() => QueueRefresh("antigravity", TimeSpan.FromMilliseconds(900)));
        _antigravityWatcher.Deleted += (_, _) => Dispatcher.Invoke(() => QueueRefresh("antigravity", TimeSpan.FromMilliseconds(900)));
        _antigravityWatcher.Renamed += (_, _) => Dispatcher.Invoke(() => QueueRefresh("antigravity", TimeSpan.FromMilliseconds(900)));
        PostJson("""{"type":"watcher","source":"antigravity","active":true,"message":"正在监听 Antigravity sessions JSONL 缓存。"}""");
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
        foreach (var source in targets)
            await RefreshSourceAsync(source).ConfigureAwait(true);
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
        _scanCts?.Cancel();
        _scanCts = new CancellationTokenSource();
        try
        {
            switch (source)
            {
                case "codex":
                    _viewModel.StatusText = "正在扫描 Codex 用量...";
                    PostJson("""{"type":"status","source":"codex","status":"scanning","message":"正在扫描 Codex 会话日志..."}""");
                    var codexPayload = await _codexUsageService.GenerateAsync(_codexSessionsPath, _scanCts.Token).ConfigureAwait(true);
                    _viewModel.StatusText = "Codex 用量统计已刷新";
                    PostJson($$"""{"type":"codexData","payload":{{codexPayload}}}""");
                    PostJson("""{"type":"status","source":"codex","status":"idle","message":"已同步 Codex 用量。"}""");
                    break;
                case "cursor":
                    _viewModel.StatusText = "正在扫描 Cursor 用量...";
                    PostJson("""{"type":"status","source":"cursor","status":"scanning","message":"正在扫描 Cursor 缓存并尝试同步..."}""");
                    var cursorPayload = await _cursorUsageService.GenerateAsync(_cursorCachePath, sync: true, cancellationToken: _scanCts.Token).ConfigureAwait(true);
                    var cursorStatusMessage = BuildCursorStatusMessage(cursorPayload);
                    _viewModel.StatusText = cursorStatusMessage;
                    PostJson($$"""{"type":"cursorData","payload":{{cursorPayload}}}""");
                    PostJson($$"""{"type":"status","source":"cursor","status":"idle","message":{{JsonSerializer.Serialize(cursorStatusMessage)}}}""");
                    break;
                case "antigravity":
                    _viewModel.StatusText = "正在扫描 Antigravity 用量...";
                    PostJson("""{"type":"status","source":"antigravity","status":"scanning","message":"正在从 Antigravity CLI 同步并扫描本地缓存..."}""");
                    var antigravityPayload = await _antigravityUsageService.GenerateAsync(_antigravityCachePath, sync: true, _scanCts.Token).ConfigureAwait(true);
                    var antigravityStatusMessage = BuildAntigravityStatusMessage(antigravityPayload);
                    _viewModel.StatusText = antigravityStatusMessage;
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
            if (_pendingRefresh.Count > 0)
                QueueRefresh(_pendingRefresh.First(), TimeSpan.FromMilliseconds(50));
        }
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
                return $"已同步 Antigravity 用量（{recordCount} 条记录）。";
            if (root.TryGetProperty("sync", out var syncElement) && syncElement.ValueKind == JsonValueKind.Object &&
                syncElement.TryGetProperty("error", out var errorElement) && errorElement.ValueKind == JsonValueKind.String)
            {
                var error = errorElement.GetString();
                if (!string.IsNullOrWhiteSpace(error))
                    return error;
            }
            if (string.Equals(dataStatus, "parse_empty", StringComparison.Ordinal))
                return "已连接 Antigravity，但未解析到有效用量记录。";
            if (string.Equals(dataStatus, "sync_failed", StringComparison.Ordinal))
                return "Antigravity 同步失败，请运行 agy CLI 或确认本地缓存已有数据。";
            if (string.Equals(dataStatus, "empty", StringComparison.Ordinal))
                return "暂无 Antigravity 用量，请运行 agy CLI 对话后刷新。";
            return "Antigravity 扫描完成，但未找到可用用量。";
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
            if (string.Equals(dataStatus, "ok", StringComparison.Ordinal) && recordCount > 0)
                return $"已同步 Cursor 用量（{recordCount} 条记录）。";
            if (root.TryGetProperty("sync", out var syncElement) && syncElement.ValueKind == JsonValueKind.Object &&
                syncElement.TryGetProperty("error", out var errorElement) && errorElement.ValueKind == JsonValueKind.String)
            {
                var error = errorElement.GetString();
                if (!string.IsNullOrWhiteSpace(error))
                    return error;
            }
            if (string.Equals(dataStatus, "parse_empty", StringComparison.Ordinal))
                return "CSV 已同步，但未解析到有效用量行。";
            if (string.Equals(dataStatus, "empty", StringComparison.Ordinal))
                return "暂无 Cursor 用量数据，请配置 Session Token 后刷新。";
            return "Cursor 扫描完成，但未找到可用用量。";
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

    private void PostJson(string json)
    {
        if (DashboardWebView.CoreWebView2 is null)
            return;
        DashboardWebView.CoreWebView2.PostWebMessageAsJson(json);
    }
}
