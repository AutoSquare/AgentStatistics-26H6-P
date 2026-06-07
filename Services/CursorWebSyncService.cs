using System.IO;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;

namespace AgentStatistics.Services;

/// <summary>
/// 通过 WebView2 加载 Cursor Dashboard，在同源上下文按官网格式请求用量 API。
/// </summary>
public sealed class CursorWebSyncService
{
    private enum SyncAttemptResult
    {
        NotAuthenticated,
        AuthenticatedFailed,
        Success,
    }

    private readonly CursorDashboardAuthService _dashboardAuthService = new();

    private const string DashboardOrigin = "https://cursor.com";
    public const string DashboardSpendingUrl = "https://cursor.com/cn/dashboard/spending";
    private const string UsageEventsApiUrl = "https://cursor.com/api/dashboard/get-filtered-usage-events";
    private const string UsageSummaryApiUrl = "https://cursor.com/api/usage-summary";
    private const string AuthMeApiUrl = "https://cursor.com/api/auth/me";
    private const string UsageCsvExportUrl = "https://cursor.com/api/dashboard/export-usage-events-csv?strategy=tokens";
    private const string WebUsageSummaryFileName = "cursor_web_usage_summary.json";
    private const string UsageSyncStatusFileName = "cursor_usage_sync_status.json";
    private const string UsageAccountFileName = "usage-account.json";
    private const string UsageJsonFileName = "usage.json";
    private const int DefaultPageSize = 100;
    private const int MaxPages = 200;
    private const int CheckpointWaitMs = 45000;
    private const int AuthBackgroundWaitMs = 12000;
    private const int AuthInteractiveWaitMs = 120000;
    private const int CaptureWaitMs = 20000;

    /// <summary>
    /// 在一次 Dashboard 会话中同步用量 JSON 与 usage-summary。
    /// </summary>
    /// <param name="reuseCurrentNavigation">为 true 时若当前页已在 Dashboard 路由则不再强制 Navigate，用于登录浮层「完成登录」。</param>
    /// <param name="tryBootstrapCandidates">为 true 时在官网 Cookie 失效时回退 IDE / 浏览器 / 凭据候选并注入 Cookie。</param>
    /// <param name="bootstrapWaitSeconds">引导脚本等待浏览器 Cookie 落盘的秒数。</param>
    public async Task<bool> TrySyncDashboardAsync(
        string cacheDir,
        string appDataDir,
        CoreWebView2 webView,
        string? sessionToken = null,
        CancellationToken cancellationToken = default,
        bool reuseCurrentNavigation = false,
        bool tryBootstrapCandidates = true,
        int bootstrapWaitSeconds = 0)
    {
        if (string.IsNullOrWhiteSpace(cacheDir) || string.IsNullOrWhiteSpace(appDataDir) || webView is null)
            return false;

        try
        {
            if (ShouldPreferEdgeDevTools(appDataDir)
                && await TrySyncEdgeDevToolsFallbackAsync(cacheDir, appDataDir, cancellationToken, preferFastTimeout: true).ConfigureAwait(false))
            {
                return true;
            }

            var currentSessionResult = await TrySyncDashboardWithTokenAsync(
                    cacheDir,
                    appDataDir,
                    webView,
                    string.IsNullOrWhiteSpace(sessionToken) ? null : CursorTokenNormalizer.Normalize(sessionToken),
                    "website-session",
                    null,
                    null,
                    cancellationToken,
                    reuseCurrentNavigation)
                .ConfigureAwait(true);
            if (currentSessionResult == SyncAttemptResult.Success)
                return true;

            if (await TrySyncEdgeDevToolsFallbackAsync(cacheDir, appDataDir, cancellationToken).ConfigureAwait(false))
                return true;

            var attempts = new List<(string? Token, string Source, string? AccountId, string? Email)>();

            if (tryBootstrapCandidates)
            {
                var bootstrap = await _dashboardAuthService.BootstrapAsync(
                        launchBrowser: false,
                        waitSeconds: bootstrapWaitSeconds,
                        cancellationToken)
                    .ConfigureAwait(true);
                CursorWebSyncLog.Write(
                    $"dashboard bootstrap candidates={bootstrap.Candidates.Count} sources={string.Join(",", bootstrap.Candidates.Select(c => c.Source ?? "bootstrap").Distinct(StringComparer.OrdinalIgnoreCase))}");
                foreach (var candidate in bootstrap.Candidates)
                {
                    var source = candidate.Source ?? "bootstrap";
                    attempts.Add((candidate.Token, source, candidate.AccountId, candidate.Email));
                }
            }

            var seenTokens = new HashSet<string>(StringComparer.Ordinal);
            foreach (var (token, source, accountId, email) in attempts)
            {
                var normalized = string.IsNullOrWhiteSpace(token) ? null : CursorTokenNormalizer.Normalize(token);
                if (!string.IsNullOrWhiteSpace(normalized) && !seenTokens.Add(normalized))
                    continue;

                var result = await TrySyncDashboardWithTokenAsync(
                        cacheDir,
                        appDataDir,
                        webView,
                        normalized,
                        source,
                        accountId,
                        email,
                        cancellationToken,
                        reuseCurrentNavigation && string.IsNullOrWhiteSpace(normalized))
                    .ConfigureAwait(true);
                if (result == SyncAttemptResult.Success)
                    return true;
            }

            CursorWebSyncLog.Write("dashboard sync failed after all session candidates");
            return false;
        }
        catch (Exception ex) when (ex is COMException or InvalidCastException or WebView2RuntimeNotFoundException)
        {
            CursorWebSyncLog.Write($"dashboard sync webview runtime error: {ex.Message}");
            return false;
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception ex)
        {
            CursorWebSyncLog.Write($"dashboard sync error: {ex.Message}");
            return false;
        }
    }

    /// <summary>
    /// 尝试将 Cursor 云端用量 JSON 写入 cursor-cache 目录中的 <c>usage.json</c>。
    /// </summary>
    public Task<bool> TrySyncUsageJsonAsync(
        string cacheDir,
        CoreWebView2 webView,
        string? sessionToken = null,
        CancellationToken cancellationToken = default) =>
        TrySyncDashboardAsync(
            cacheDir,
            UserSettingsStore.AppDataDirectory,
            webView,
            sessionToken,
            cancellationToken);

    /// <summary>
    /// 兼容旧路径。
    /// </summary>
    public Task<bool> TrySyncUsageCsvAsync(
        string cacheDir,
        CoreWebView2 webView,
        string? sessionToken = null,
        CancellationToken cancellationToken = default) =>
        TrySyncUsageJsonAsync(cacheDir, webView, sessionToken, cancellationToken);

    /// <summary>
    /// 通过 Dashboard 同源 fetch 拉取 usage-summary。
    /// </summary>
    public Task<bool> TryRefreshUsageSummaryAsync(
        string appDataDir,
        CoreWebView2 webView,
        string? sessionToken = null,
        CancellationToken cancellationToken = default) =>
        TrySyncDashboardAsync(
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config", "tokscale", "cursor-cache"),
            appDataDir,
            webView,
            sessionToken,
            cancellationToken);

    private async Task<SyncAttemptResult> TrySyncDashboardWithTokenAsync(
        string cacheDir,
        string appDataDir,
        CoreWebView2 webView,
        string? token,
        string? source,
        string? accountId,
        string? email,
        CancellationToken cancellationToken,
        bool reuseCurrentNavigation = false)
    {
        CursorWebSyncLog.Write($"dashboard sync try source={source ?? "unknown"}");
        var capture = new CursorDashboardCapture();
        var context = await OpenDashboardContextAsync(
            webView,
            token,
            capture,
            cancellationToken,
            reuseCurrentNavigation).ConfigureAwait(true);
        if (!context.Ready)
        {
            CursorWebSyncLog.Write(
                $"dashboard not ready source={source ?? "unknown"} href={context.Href ?? "(unknown)"} checkpoint={context.OnCheckpoint} auth={context.OnAuthPage} cookie={context.HasSessionCookie} note={context.Note ?? capture.LastNote ?? "unknown"}");
            return SyncAttemptResult.NotAuthenticated;
        }

        var currentUser = await FetchCurrentUserFallbackAsync(webView, capture, cancellationToken).ConfigureAwait(true);
        var authenticatedAccountId = ReadString(currentUser, "sub");
        if (string.IsNullOrWhiteSpace(authenticatedAccountId))
        {
            CursorWebSyncLog.Write(
                $"auth/me rejected source={source ?? "unknown"} href={context.Href ?? "(unknown)"} cookie={context.HasSessionCookie} note={capture.LastNote ?? "empty user"}");
            return SyncAttemptResult.NotAuthenticated;
        }

        accountId = authenticatedAccountId ?? accountId;
        email = ReadString(currentUser, "email") ?? email;
        await WriteUsageAccountAsync(cacheDir, accountId, email, true, cancellationToken).ConfigureAwait(true);

        var events = capture.Events;
        if (events.Count == 0 || IsCapturedUsagePartial(capture))
        {
            if (events.Count > 0)
            {
                CursorWebSyncLog.Write(
                    $"captured usage-events partial events={events.Count} total={capture.TotalUsageEventsCount?.ToString() ?? "?"}; fetching pages");
            }

            var fetched = await FetchUsageEventsFallbackAsync(webView, capture, cancellationToken).ConfigureAwait(true);
            if (fetched.Count > events.Count)
                events = fetched;
        }

        var usageOk = false;
        var expectedCount = capture.TotalUsageEventsCount;
        var usageComplete = events.Count > 0
                            && (expectedCount is null || events.Count >= expectedCount.Value);
        if (usageComplete)
        {
            accountId ??= !string.IsNullOrWhiteSpace(token)
                ? CursorTokenNormalizer.DeriveAccountId(token)
                : ExtractOwningUser(events);
            await WriteUsageJsonAsync(
                cacheDir,
                events,
                accountId,
                email,
                cancellationToken).ConfigureAwait(true);
            if (!string.IsNullOrWhiteSpace(accountId))
                await WriteUsageAccountAsync(cacheDir, accountId, email, true, cancellationToken).ConfigureAwait(true);
            await WriteUsageSyncStatusAsync(
                appDataDir,
                accountId,
                "ok",
                events.Count,
                expectedCount,
                null,
                cancellationToken).ConfigureAwait(true);
            CursorWebSyncLog.Write($"usage-json ok events={events.Count} source={capture.LastNote ?? source ?? "same-origin-fetch"}");
            usageOk = true;
        }
        else if (events.Count > 0)
        {
            var csvText = await FetchUsageCsvFallbackAsync(webView, capture, cancellationToken).ConfigureAwait(true);
            if (!string.IsNullOrWhiteSpace(csvText))
            {
                await WriteTextAtomicallyAsync(
                    Path.Combine(cacheDir, "usage.csv"),
                    csvText,
                    cancellationToken).ConfigureAwait(true);
                if (!string.IsNullOrWhiteSpace(accountId))
                    await WriteUsageAccountAsync(cacheDir, accountId, email, true, cancellationToken).ConfigureAwait(true);
                await WriteUsageSyncStatusAsync(
                    appDataDir,
                    accountId,
                    "ok",
                    CountCsvRows(csvText),
                    expectedCount,
                    "官网 JSON 分页不完整，已使用官网 CSV 完整导出。",
                    cancellationToken).ConfigureAwait(true);
                CursorWebSyncLog.Write(
                    $"usage-csv fallback ok rows={CountCsvRows(csvText)} jsonEvents={events.Count} total={expectedCount?.ToString() ?? "?"}");
                usageOk = true;
            }

            var rejectedAccountId = accountId ?? (!string.IsNullOrWhiteSpace(token)
                ? CursorTokenNormalizer.DeriveAccountId(token)
                : ExtractOwningUser(events));
            if (!usageOk)
            {
                await WriteUsageSyncStatusAsync(
                    appDataDir,
                    rejectedAccountId,
                    "partial",
                    events.Count,
                    expectedCount,
                    capture.LastNote ?? "官网分页结果不完整，已保留上一次完整用量快照。",
                    cancellationToken).ConfigureAwait(true);
                CursorWebSyncLog.Write(
                    $"usage sync rejected partial events={events.Count} total={expectedCount?.ToString() ?? "?"} note={capture.LastNote ?? "unknown"}; keeping previous complete snapshot");
            }
        }
        else
        {
            CursorWebSyncLog.Write(
                $"usage sync failed source={source ?? "unknown"} events=0 href={context.Href ?? "(unknown)"} note={capture.LastNote ?? context.Note ?? "no events"} auth={context.OnAuthPage} cookie={context.HasSessionCookie}");
        }

        JsonElement summary = default;
        if (capture.UsageSummary is { ValueKind: JsonValueKind.Object } captured)
            summary = captured;
        else
            summary = await FetchUsageSummaryFallbackAsync(webView, capture, cancellationToken).ConfigureAwait(true);

        if (summary.ValueKind == JsonValueKind.Object)
        {
            Directory.CreateDirectory(appDataDir);
            var target = Path.Combine(appDataDir, WebUsageSummaryFileName);
            var json =
                "{\n  \"fetchedAt\": " + JsonSerializer.Serialize(DateTime.UtcNow.ToString("O")) +
                ",\n  \"summary\": " + summary.GetRawText() + "\n}\n";
            await WriteTextAtomicallyAsync(target, json, cancellationToken).ConfigureAwait(true);
            CursorWebSyncLog.Write("usage-summary ok");
        }
        else
        {
            CursorWebSyncLog.Write(
                $"usage-summary failed source={source ?? "unknown"} href={context.Href ?? "(unknown)"} note={capture.LastNote ?? context.Note ?? "empty"} auth={context.OnAuthPage} cookie={context.HasSessionCookie}");
        }

        if (usageOk)
            return SyncAttemptResult.Success;
        return !string.IsNullOrWhiteSpace(authenticatedAccountId) || events.Count > 0 || !string.IsNullOrWhiteSpace(token)
            ? SyncAttemptResult.AuthenticatedFailed
            : SyncAttemptResult.NotAuthenticated;
    }

    private static bool IsCapturedUsagePartial(CursorDashboardCapture capture) =>
        capture.TotalUsageEventsCount is { } total && capture.Events.Count > 0 && capture.Events.Count < total;

    private sealed record DashboardContext
    {
        public bool Ready { get; init; }
        public bool OnCheckpoint { get; init; }
        public bool OnAuthPage { get; init; }
        public bool HasSessionCookie { get; init; }
        public string? Href { get; init; }
        public string? Note { get; init; }
    }

    private async Task<DashboardContext> OpenDashboardContextAsync(
        CoreWebView2 webView,
        string? token,
        CursorDashboardCapture capture,
        CancellationToken cancellationToken,
        bool reuseCurrentNavigation = false)
    {
        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(TimeSpan.FromSeconds(150));

        if (!string.IsNullOrWhiteSpace(token))
            await ApplySessionTokenAsync(webView, token).ConfigureAwait(true);

        var navigationCompleted = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs args)
        {
            if (args.IsSuccess)
                navigationCompleted.TrySetResult(true);
        }

        void OnResponseReceived(object? sender, CoreWebView2WebResourceResponseReceivedEventArgs args)
        {
            if (!IsDashboardApi(args.Request.Uri))
                return;
            _ = ReadResponseBodyAsync(args, capture);
        }

        webView.WebResourceResponseReceived += OnResponseReceived;
        webView.NavigationCompleted += OnNavigationCompleted;
        try
        {
            var shouldNavigate = true;
            if (reuseCurrentNavigation)
            {
                var current = await ProbeDashboardPageAsync(webView).ConfigureAwait(true);
                shouldNavigate = !IsDashboardRoute(current.Href) && !IsAuthenticatorRoute(current.Href);
            }

            if (shouldNavigate)
            {
                webView.Navigate(DashboardSpendingUrl);
                using var _ = timeoutCts.Token.Register(() => navigationCompleted.TrySetResult(false));
                await navigationCompleted.Task.ConfigureAwait(true);
            }

            var authWaitMs = reuseCurrentNavigation ? AuthInteractiveWaitMs : AuthBackgroundWaitMs;
            var context = await WaitForDashboardContextAsync(webView, timeoutCts.Token, authWaitMs).ConfigureAwait(true);
            capture.PageHref = context.Href;
            capture.SawAuthPage = context.OnAuthPage;

            if (!context.Ready)
                return context;

            await WaitForCaptureAsync(webView, capture, timeoutCts.Token).ConfigureAwait(true);
            var hasCookie = await HasDashboardSessionCookieAsync(webView).ConfigureAwait(true);
            return context with { HasSessionCookie = hasCookie };
        }
        finally
        {
            webView.WebResourceResponseReceived -= OnResponseReceived;
            webView.NavigationCompleted -= OnNavigationCompleted;
        }
    }

    private async Task<DashboardContext> WaitForDashboardContextAsync(
        CoreWebView2 webView,
        CancellationToken cancellationToken,
        int authWaitMs)
    {
        var deadline = Environment.TickCount64 + CheckpointWaitMs;
        var authDeadline = Environment.TickCount64 + authWaitMs;
        DashboardContext? last = null;
        while (Environment.TickCount64 < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            last = await ProbeDashboardPageAsync(webView).ConfigureAwait(true);
            if (last.Ready)
                return last;
            if (last.OnAuthPage && IsAuthenticatorRoute(last.Href) && Environment.TickCount64 >= authDeadline)
            {
                return last with { Ready = false, Note = "authenticator redirect; try next session candidate" };
            }
            await Task.Delay(1000, cancellationToken).ConfigureAwait(true);
        }

        return (last ?? new DashboardContext()) with
        {
            Ready = false,
            Note = last?.OnAuthPage == true
                ? "dashboard auth timeout; waiting for browser SSO"
                : last?.OnCheckpoint == true
                    ? "vercel checkpoint timeout"
                    : "dashboard not ready",
        };
    }

    private static async Task<DashboardContext> ProbeDashboardPageAsync(CoreWebView2 webView)
    {
        var raw = await webView.ExecuteScriptAsync(
            """
            (() => {
              const href = location.href || '';
              const text = document.body ? document.body.innerText : '';
              const title = document.title || '';
              const checkpoint = /Vercel Security Checkpoint|We're verifying your browser|Enable JavaScript to continue|正在验证/i.test(title + ' ' + text.slice(0, 600));
              const onAuthHost = /authenticator\.cursor\.sh/i.test(href);
              const auth = onAuthHost || /sign in|log in|登录|authenticate|Continue with|Get started/i.test((title + ' ' + text).slice(0, 900));
              const onCursor = /cursor\.com/i.test(href);
              const dashboard = onCursor && /\/dashboard\b/i.test(href);
              const ready = dashboard && !checkpoint && !auth;
              return JSON.stringify({ checkpoint, auth, dashboard, ready, href, title: title.slice(0, 120) });
            })();
            """).ConfigureAwait(true);

        var decoded = UnwrapExecuteScriptPayload(raw);
        if (string.IsNullOrWhiteSpace(decoded))
        {
            return new DashboardContext
            {
                Ready = false,
                Note = "probe script returned empty",
            };
        }

        using var document = JsonDocument.Parse(decoded);
        var root = document.RootElement;
        var checkpoint = root.TryGetProperty("checkpoint", out var checkpointElement)
                         && checkpointElement.ValueKind == JsonValueKind.True;
        var auth = root.TryGetProperty("auth", out var authElement)
                   && authElement.ValueKind == JsonValueKind.True;
        var dashboard = root.TryGetProperty("dashboard", out var dashboardElement)
                        && dashboardElement.ValueKind == JsonValueKind.True;
        var ready = root.TryGetProperty("ready", out var readyElement)
                    && readyElement.ValueKind == JsonValueKind.True;
        var href = root.TryGetProperty("href", out var hrefElement) ? hrefElement.GetString() : null;
        var hasCookie = await HasDashboardSessionCookieAsync(webView).ConfigureAwait(true);
        var onDashboardRoute = IsDashboardRoute(href);
        var effectiveReady = ready;
        return new DashboardContext
        {
            Ready = effectiveReady,
            OnCheckpoint = checkpoint,
            OnAuthPage = auth && !effectiveReady,
            HasSessionCookie = hasCookie,
            Href = href,
            Note = checkpoint ? "vercel checkpoint" : onDashboardRoute ? null : "not on dashboard route",
        };
    }

    private static bool IsDashboardRoute(string? href) =>
        !string.IsNullOrWhiteSpace(href)
        && href.Contains("cursor.com", StringComparison.OrdinalIgnoreCase)
        && href.Contains("/dashboard", StringComparison.OrdinalIgnoreCase);

    private static bool IsAuthenticatorRoute(string? href) =>
        !string.IsNullOrWhiteSpace(href)
        && href.Contains("authenticator.cursor.sh", StringComparison.OrdinalIgnoreCase);

    private static async Task<bool> HasDashboardSessionCookieAsync(CoreWebView2 webView) =>
        !string.IsNullOrWhiteSpace(await ReadDashboardCookieAsync(webView).ConfigureAwait(true));

    private static async Task ReadResponseBodyAsync(
        CoreWebView2WebResourceResponseReceivedEventArgs args,
        CursorDashboardCapture capture)
    {
        try
        {
            var status = args.Response.StatusCode;
            await using var stream = await args.Response.GetContentAsync().ConfigureAwait(true);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            var body = await reader.ReadToEndAsync().ConfigureAwait(true);
            capture.TryAbsorbResponse(args.Request.Uri, status, body);
        }
        catch (COMException ex)
        {
            capture.LastNote = $"response read failed: {ex.Message}";
        }
        catch (IOException ex)
        {
            capture.LastNote = $"response read failed: {ex.Message}";
        }
    }

    private static async Task WaitForCaptureAsync(
        CoreWebView2 webView,
        CursorDashboardCapture capture,
        CancellationToken cancellationToken)
    {
        var deadline = Environment.TickCount64 + CaptureWaitMs;
        while (Environment.TickCount64 < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (capture.Events.Count > 0 && capture.UsageSummary is { ValueKind: JsonValueKind.Object })
                return;
            await Task.Delay(500, cancellationToken).ConfigureAwait(true);
        }

        if (capture.Events.Count == 0)
            capture.LastNote ??= "capture timeout waiting usage-events";
        if (capture.UsageSummary is not { ValueKind: JsonValueKind.Object })
            capture.LastNote ??= "capture timeout waiting usage-summary";
    }

    private static async Task<List<JsonElement>> FetchUsageEventsFallbackAsync(
        CoreWebView2 webView,
        CursorDashboardCapture capture,
        CancellationToken cancellationToken)
    {
        var allEvents = capture.Events.ToList();
        int? totalCount = capture.TotalUsageEventsCount;
        var hasMore = allEvents.Count > 0;
        var firstPage = allEvents.Count > 0 ? 2 : 1;
        for (var page = firstPage; page <= MaxPages && hasMore; page++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var pageJson = await FetchUsagePageScriptAsync(
                    webView,
                    page,
                    DefaultPageSize,
                    useEmptyBody: page == 1)
                .ConfigureAwait(true);
            hasMore = TryParseFetchPage(pageJson, capture, page, allEvents, ref totalCount);
            if (totalCount is not null && allEvents.Count >= totalCount.Value)
                break;
        }

        return allEvents;
    }

    private static bool TryParseFetchPage(
        string? pageJson,
        CursorDashboardCapture capture,
        int page,
        List<JsonElement> allEvents,
        ref int? totalCount)
    {
        if (string.IsNullOrWhiteSpace(pageJson))
            return false;

        using var document = JsonDocument.Parse(pageJson);
        var root = document.RootElement;
        if (root.TryGetProperty("ok", out var okElement) && okElement.ValueKind == JsonValueKind.False)
        {
            var status = root.TryGetProperty("status", out var statusElement) ? statusElement.GetInt32() : 0;
            var text = root.TryGetProperty("text", out var textElement) ? textElement.GetString() : null;
            var error = root.TryGetProperty("error", out var errorElement) ? errorElement.GetString() : null;
            capture.LastNote =
                $"fetch usage-events page={page} status={status} error={error ?? ""} text={(text ?? "").Substring(0, Math.Min(160, (text ?? "").Length))}";
            return false;
        }

        JsonElement payload = root;
        if (root.TryGetProperty("json", out var wrapped))
            payload = wrapped;

        if (totalCount is null && payload.TryGetProperty("totalUsageEventsCount", out var totalElement))
        {
            if (totalElement.ValueKind == JsonValueKind.Number && totalElement.TryGetInt32(out var totalNumber))
                totalCount = totalNumber;
            else if (totalElement.ValueKind == JsonValueKind.String
                     && int.TryParse(totalElement.GetString(), out var parsed))
                totalCount = parsed;
        }

        var batch = ExtractUsageEvents(payload);
        if (batch.Count == 0)
        {
            capture.LastNote ??= $"fetch usage-events page={page} empty batch";
            return false;
        }

        allEvents.AddRange(batch);
        capture.LastNote = $"same-origin-fetch page={page} batch={batch.Count}";
        return batch.Count >= DefaultPageSize;
    }

    private static async Task<JsonElement> FetchUsageSummaryFallbackAsync(
        CoreWebView2 webView,
        CursorDashboardCapture capture,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var script = $$"""
            (async () => {
              try {
                const response = await fetch('{{UsageSummaryApiUrl}}', {
                  method: 'GET',
                  credentials: 'include',
                  headers: { 'Accept': 'application/json' }
                });
                const text = await response.text();
                if (!response.ok) {
                  return JSON.stringify({ ok: false, status: response.status, text: text.slice(0, 400) });
                }
                return JSON.stringify({ ok: true, json: JSON.parse(text) });
              } catch (error) {
                return JSON.stringify({ ok: false, error: String(error) });
              }
            })();
            """;
        var raw = await webView.ExecuteScriptAsync(script).ConfigureAwait(true);
        var decoded = UnwrapExecuteScriptPayload(raw);
        if (string.IsNullOrWhiteSpace(decoded))
            return default;

        using var document = JsonDocument.Parse(decoded);
        var root = document.RootElement;
        if (root.TryGetProperty("ok", out var okElement) && okElement.ValueKind == JsonValueKind.False)
        {
            var status = root.TryGetProperty("status", out var statusElement) ? statusElement.GetInt32() : 0;
            var text = root.TryGetProperty("text", out var textElement) ? textElement.GetString() : null;
            capture.LastNote = $"fetch usage-summary status={status} text={(text ?? "").Substring(0, Math.Min(160, (text ?? "").Length))}";
            return default;
        }
        if (root.TryGetProperty("json", out var payload) && payload.ValueKind == JsonValueKind.Object)
            return payload.Clone();
        return default;
    }

    private static async Task<JsonElement> FetchCurrentUserFallbackAsync(
        CoreWebView2 webView,
        CursorDashboardCapture capture,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var result = await FetchJsonGetAsync(webView, AuthMeApiUrl).ConfigureAwait(true);
        if (result.ValueKind == JsonValueKind.Object)
            return result;
        capture.LastNote = "fetch auth/me failed";
        return default;
    }

    private static async Task<string?> FetchUsageCsvFallbackAsync(
        CoreWebView2 webView,
        CursorDashboardCapture capture,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var script = $$"""
            (async () => {
              try {
                const response = await fetch('{{UsageCsvExportUrl}}', {
                  method: 'GET',
                  credentials: 'include',
                  headers: { 'Accept': 'text/csv,*/*' }
                });
                const text = await response.text();
                return JSON.stringify({
                  ok: response.ok,
                  status: response.status,
                  text: response.ok ? text : text.slice(0, 400)
                });
              } catch (error) {
                return JSON.stringify({ ok: false, error: String(error) });
              }
            })();
            """;
        var raw = await webView.ExecuteScriptAsync(script).ConfigureAwait(true);
        var decoded = UnwrapExecuteScriptPayload(raw);
        if (string.IsNullOrWhiteSpace(decoded))
            return null;
        using var document = JsonDocument.Parse(decoded);
        var root = document.RootElement;
        if (root.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.True
            && root.TryGetProperty("text", out var text) && text.ValueKind == JsonValueKind.String)
        {
            var csv = text.GetString();
            return CountCsvRows(csv) > 0 ? csv : null;
        }
        capture.LastNote = $"fetch usage-csv failed status={ReadInt(root, "status")} error={ReadString(root, "error") ?? ReadString(root, "text") ?? "unknown"}";
        return null;
    }

    private static async Task<JsonElement> FetchJsonGetAsync(CoreWebView2 webView, string url)
    {
        var script = $$"""
            (async () => {
              try {
                const response = await fetch('{{url}}', {
                  method: 'GET',
                  credentials: 'include',
                  headers: { 'Accept': 'application/json' }
                });
                const text = await response.text();
                return JSON.stringify({ ok: response.ok, status: response.status, json: response.ok ? JSON.parse(text) : null });
              } catch (error) {
                return JSON.stringify({ ok: false, error: String(error) });
              }
            })();
            """;
        var raw = await webView.ExecuteScriptAsync(script).ConfigureAwait(true);
        var decoded = UnwrapExecuteScriptPayload(raw);
        if (string.IsNullOrWhiteSpace(decoded))
            return default;
        using var document = JsonDocument.Parse(decoded);
        var root = document.RootElement;
        if (root.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.True
            && root.TryGetProperty("json", out var payload) && payload.ValueKind == JsonValueKind.Object)
            return payload.Clone();
        return default;
    }

    private static async Task<string?> FetchUsagePageScriptAsync(
        CoreWebView2 webView,
        int page,
        int pageSize,
        bool useEmptyBody)
    {
        var body = useEmptyBody ? "{}" : BuildUsageEventsRequestBody(page, pageSize);
        var escapedBody = JsonSerializer.Serialize(body);
        var script = $$"""
            (async () => {
              try {
                const response = await fetch('{{UsageEventsApiUrl}}', {
                  method: 'POST',
                  credentials: 'include',
                  headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Origin': '{{DashboardOrigin}}',
                    'Referer': '{{DashboardSpendingUrl}}'
                  },
                  body: {{escapedBody}}
                });
                const text = await response.text();
                if (!response.ok) {
                  return JSON.stringify({ ok: false, status: response.status, text: text.slice(0, 400) });
                }
                return JSON.stringify({ ok: true, json: JSON.parse(text) });
              } catch (error) {
                return JSON.stringify({ ok: false, error: String(error) });
              }
            })();
            """;
        var raw = await webView.ExecuteScriptAsync(script).ConfigureAwait(true);
        return UnwrapExecuteScriptPayload(raw);
    }

    private static string BuildUsageEventsRequestBody(int page, int pageSize)
    {
        var end = DateTimeOffset.UtcNow;
        var start = end.AddDays(-30);
        var payload = new
        {
            page,
            pageSize,
            startDate = start.ToUnixTimeMilliseconds().ToString(),
            endDate = end.ToUnixTimeMilliseconds().ToString(),
        };
        return JsonSerializer.Serialize(payload);
    }

    private static string? UnwrapExecuteScriptPayload(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw) || string.Equals(raw, "null", StringComparison.OrdinalIgnoreCase))
            return null;
        try
        {
            using var document = JsonDocument.Parse(raw);
            return document.RootElement.ValueKind switch
            {
                JsonValueKind.String => document.RootElement.GetString(),
                JsonValueKind.Object or JsonValueKind.Array => raw,
                _ => null,
            };
        }
        catch (JsonException)
        {
            return raw;
        }
    }

    private static async Task<string?> ReadDashboardCookieAsync(CoreWebView2 webView)
    {
        var cookies = await webView.CookieManager.GetCookiesAsync(DashboardOrigin).ConfigureAwait(true);
        foreach (var cookie in cookies)
        {
            if (!string.Equals(cookie.Name, "WorkosCursorSessionToken", StringComparison.Ordinal))
                continue;
            var normalized = CursorTokenNormalizer.Normalize(cookie.Value);
            if (!string.IsNullOrWhiteSpace(normalized))
                return normalized;
        }
        return null;
    }

    private static async Task WriteUsageJsonAsync(
        string cacheDir,
        IReadOnlyList<JsonElement> events,
        string? accountId,
        string? email,
        CancellationToken cancellationToken)
    {
        Directory.CreateDirectory(cacheDir);
        var target = Path.Combine(cacheDir, UsageJsonFileName);
        await WriteTextAtomicallyAsync(
            target,
            BuildUsageJsonText(events, accountId, email),
            cancellationToken).ConfigureAwait(true);
    }

    private static string BuildUsageJsonText(
        IReadOnlyList<JsonElement> events,
        string? accountId,
        string? email)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions { Indented = true }))
        {
            writer.WriteStartObject();
            writer.WriteNumber("version", 1);
            writer.WriteString("source", "webview-json");
            writer.WriteString("syncedAt", DateTime.UtcNow.ToString("O"));
            if (!string.IsNullOrWhiteSpace(accountId))
                writer.WriteString("accountId", accountId);
            if (!string.IsNullOrWhiteSpace(email))
                writer.WriteString("email", email);
            writer.WriteNumber("totalEvents", events.Count);
            writer.WritePropertyName("events");
            writer.WriteStartArray();
            foreach (var item in events)
                item.WriteTo(writer);
            writer.WriteEndArray();
            writer.WriteEndObject();
        }

        var json = Encoding.UTF8.GetString(stream.ToArray());
        return json.EndsWith('\n') ? json : json + "\n";
    }

    private static string? ExtractOwningUser(IReadOnlyList<JsonElement> events)
    {
        foreach (var item in events)
        {
            if (item.TryGetProperty("owningUser", out var value)
                && value.ValueKind == JsonValueKind.String
                && !string.IsNullOrWhiteSpace(value.GetString()))
            {
                return value.GetString();
            }
        }
        return null;
    }

    private static async Task WriteTextAtomicallyAsync(string target, string content, CancellationToken cancellationToken)
    {
        var tmp = target + ".tmp";
        await File.WriteAllTextAsync(
            tmp,
            content.EndsWith('\n') ? content : content + "\n",
            new UTF8Encoding(encoderShouldEmitUTF8Identifier: false),
            cancellationToken).ConfigureAwait(true);
        File.Move(tmp, target, overwrite: true);
    }

    private static async Task WriteUsageAccountAsync(
        string cacheDir,
        string? accountId,
        string? email,
        bool isOnline,
        CancellationToken cancellationToken)
    {
        Directory.CreateDirectory(cacheDir);
        var payload = new
        {
            version = 1,
            accountId,
            email,
            isOnline,
            updatedAt = DateTime.UtcNow.ToString("O"),
        };
        await WriteTextAtomicallyAsync(
            Path.Combine(cacheDir, UsageAccountFileName),
            JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true }),
            cancellationToken).ConfigureAwait(true);
    }

    public static async Task ClearDashboardSessionAsync(CoreWebView2 webView)
    {
        webView.CookieManager.DeleteAllCookies();
        await Task.CompletedTask.ConfigureAwait(true);
    }

    /// <summary>
    /// 将引导得到的 Session Token 写入 WebView2 Cookie，供 Dashboard 同源 API 使用。
    /// </summary>
    /// <param name="webView">目标 WebView2。</param>
    /// <param name="token">Session Token 或 Cookie 片段。</param>
    private static async Task ApplySessionTokenAsync(CoreWebView2 webView, string token)
    {
        var normalized = CursorTokenNormalizer.Normalize(token);
        if (string.IsNullOrWhiteSpace(normalized))
            return;

        await ClearSessionTokenCookieAsync(webView).ConfigureAwait(true);
        var cookie = webView.CookieManager.CreateCookie(
            "WorkosCursorSessionToken",
            normalized,
            ".cursor.com",
            "/");
        cookie.IsSecure = true;
        cookie.IsHttpOnly = true;
        cookie.SameSite = CoreWebView2CookieSameSiteKind.None;
        webView.CookieManager.AddOrUpdateCookie(cookie);
    }

    private static async Task ClearSessionTokenCookieAsync(CoreWebView2 webView)
    {
        var cookies = await webView.CookieManager.GetCookiesAsync(DashboardOrigin).ConfigureAwait(true);
        foreach (var cookie in cookies)
        {
            if (string.Equals(cookie.Name, "WorkosCursorSessionToken", StringComparison.Ordinal))
                webView.CookieManager.DeleteCookie(cookie);
        }
    }

    /// <summary>
    /// 用户主动切换账号时标记官网会话离线。
    /// </summary>
    /// <param name="cacheDir">cursor-cache 目录。</param>
    /// <param name="cancellationToken">取消标记。</param>
    public static Task MarkWebsiteSessionOfflineAsync(string cacheDir, CancellationToken cancellationToken = default) =>
        WriteUsageAccountAsync(cacheDir, null, null, false, cancellationToken);

    /// <summary>
    /// 用户主动切换账号时清除上次云端同步状态，避免沿用外部浏览器来源的伪在线标记。
    /// </summary>
    /// <param name="appDataDir">AgentStatistics AppData 目录。</param>
    /// <param name="cancellationToken">取消标记。</param>
    public static Task MarkWebsiteSyncOfflineAsync(string appDataDir, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(appDataDir))
            return Task.CompletedTask;
        var path = Path.Combine(appDataDir, UsageSyncStatusFileName);
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch (IOException)
        {
        }
        return Task.CompletedTask;
    }

    private static int CountCsvRows(string? csv)
    {
        if (string.IsNullOrWhiteSpace(csv))
            return 0;
        return Math.Max(0, csv.Split('\n', StringSplitOptions.RemoveEmptyEntries).Length - 1);
    }

    private static string? ReadString(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static int ReadInt(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object
        && element.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.Number
        && value.TryGetInt32(out var number)
            ? number
            : 0;

    private static async Task WriteUsageSyncStatusAsync(
        string appDataDir,
        string? accountId,
        string status,
        int actualEvents,
        int? expectedEvents,
        string? message,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(accountId))
            return;
        Directory.CreateDirectory(appDataDir);
        var payload = new
        {
            version = 1,
            accountId,
            status,
            actualEvents,
            expectedEvents,
            message,
            updatedAt = DateTime.UtcNow.ToString("O"),
        };
        await WriteTextAtomicallyAsync(
            Path.Combine(appDataDir, UsageSyncStatusFileName),
            JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true }),
            cancellationToken).ConfigureAwait(true);
    }

    private static List<JsonElement> ExtractUsageEvents(JsonElement payload)
    {
        foreach (var key in new[] { "usageEventsDisplay", "usageEvents", "events" })
        {
            if (!payload.TryGetProperty(key, out var array) || array.ValueKind != JsonValueKind.Array)
                continue;
            return array.EnumerateArray().Where(item => item.ValueKind == JsonValueKind.Object).ToList();
        }

        return [];
    }

    private static bool IsDashboardApi(string? uri) =>
        !string.IsNullOrWhiteSpace(uri)
        && uri.Contains("cursor.com/api/", StringComparison.OrdinalIgnoreCase)
        && (uri.Contains("get-filtered-usage-events", StringComparison.OrdinalIgnoreCase)
            || uri.Contains("usage-summary", StringComparison.OrdinalIgnoreCase));

    private static bool ShouldPreferEdgeDevTools(string appDataDir)
    {
        try
        {
            var path = Path.Combine(appDataDir, UsageSyncStatusFileName);
            if (!File.Exists(path))
                return false;

            using var document = JsonDocument.Parse(File.ReadAllText(path, Encoding.UTF8));
            var root = document.RootElement;
            var status = ReadString(root, "status");
            if (!string.Equals(status, "ok", StringComparison.OrdinalIgnoreCase))
                return false;

            var source = ReadString(root, "source");
            var message = ReadString(root, "message");
            return string.Equals(source, "edge-devtools-json", StringComparison.OrdinalIgnoreCase)
                   || string.Equals(message, "Edge DevTools sync ok", StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return false;
        }
    }

    private static async Task<bool> TrySyncEdgeDevToolsFallbackAsync(
        string cacheDir,
        string appDataDir,
        CancellationToken cancellationToken,
        bool preferFastTimeout = false)
    {
        var scriptPath = Path.Combine(AppPaths.PyFolder, "cursor_edge_devtools_sync.py");
        if (!File.Exists(scriptPath) || !File.Exists(AppPaths.PythonExe))
        {
            CursorWebSyncLog.Write("edge devtools fallback skipped: sync script or Python runtime is unavailable");
            return false;
        }

        var stopwatch = Stopwatch.StartNew();
        var timeoutSeconds = preferFastTimeout ? 12 : 45;
        CursorWebSyncLog.Write(preferFastTimeout ? "edge devtools primary try" : "edge devtools fallback try");
        using var proc = new Process
        {
            StartInfo = new ProcessStartInfo
            {
                FileName = AppPaths.PythonExe,
                Arguments = $"-u \"{scriptPath}\" --cache-dir \"{cacheDir}\" --app-data-dir \"{appDataDir}\" --timeout {timeoutSeconds}",
                WorkingDirectory = AppPaths.PyFolder,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = new UTF8Encoding(false),
                StandardErrorEncoding = new UTF8Encoding(false),
            },
            EnableRaisingEvents = true,
        };
        proc.StartInfo.Environment["PYTHONIOENCODING"] = "utf-8";
        proc.StartInfo.Environment["PYTHONUTF8"] = "1";

        try
        {
            proc.Start();
            var stdoutTask = proc.StandardOutput.ReadToEndAsync(cancellationToken);
            var stderrTask = proc.StandardError.ReadToEndAsync(cancellationToken);
            await proc.WaitForExitAsync(cancellationToken).ConfigureAwait(false);

            var stdout = (await stdoutTask.ConfigureAwait(false)).Trim();
            var stderr = (await stderrTask.ConfigureAwait(false)).Trim();
            if (proc.ExitCode != 0)
            {
                CursorWebSyncLog.Write($"edge devtools sync failed elapsedMs={stopwatch.ElapsedMilliseconds} exit={proc.ExitCode} note={SummarizeProcessOutput(stderr, stdout)}");
                return false;
            }

            using var document = JsonDocument.Parse(stdout);
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("ok", out var okElement)
                || okElement.ValueKind != JsonValueKind.True)
            {
                CursorWebSyncLog.Write($"edge devtools sync rejected elapsedMs={stopwatch.ElapsedMilliseconds} note={SummarizeProcessOutput(stdout, stderr)}");
                return false;
            }

            var actual = root.TryGetProperty("actualEvents", out var actualElement) && actualElement.TryGetInt32(out var actualEvents)
                ? actualEvents.ToString()
                : "?";
            var expected = root.TryGetProperty("expectedEvents", out var expectedElement) && expectedElement.TryGetInt32(out var expectedEvents)
                ? expectedEvents.ToString()
                : "?";
            CursorWebSyncLog.Write($"edge devtools sync ok elapsedMs={stopwatch.ElapsedMilliseconds} events={actual}/{expected}");
            return true;
        }
        catch (OperationCanceledException)
        {
            TryKill(proc);
            throw;
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or JsonException)
        {
            CursorWebSyncLog.Write($"edge devtools sync error elapsedMs={stopwatch.ElapsedMilliseconds}: {ex.Message}");
            return false;
        }
    }

    private static string SummarizeProcessOutput(string primary, string secondary)
    {
        var text = string.IsNullOrWhiteSpace(primary) ? secondary : primary;
        text = text.Replace('\r', ' ').Replace('\n', ' ').Trim();
        return text.Length <= 240 ? text : text[..240];
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
}
