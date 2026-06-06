using System.IO;
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
    private readonly CursorDashboardAuthService _authService;

    /// <summary>
    /// 初始化 Dashboard 同步服务。
    /// </summary>
    /// <param name="authService">自动引导 Dashboard 登录态的服务。</param>
    public CursorWebSyncService(CursorDashboardAuthService authService)
    {
        _authService = authService;
    }

    private const string DashboardOrigin = "https://cursor.com";
    private const string DashboardUsageUrl = "https://cursor.com/cn/dashboard/usage";
    private const string UsageEventsApiUrl = "https://cursor.com/api/dashboard/get-filtered-usage-events";
    private const string UsageSummaryApiUrl = "https://cursor.com/api/usage-summary";
    private const string WebUsageSummaryFileName = "cursor_web_usage_summary.json";
    private const string UsageJsonFileName = "usage.json";
    private const int DefaultPageSize = 100;
    private const int MaxPages = 200;
    private const int CheckpointWaitMs = 45000;
    private const int CaptureWaitMs = 20000;

    /// <summary>
    /// 在一次 Dashboard 会话中同步用量 JSON 与 usage-summary。
    /// </summary>
    public async Task<bool> TrySyncDashboardAsync(
        string cacheDir,
        string appDataDir,
        CoreWebView2 webView,
        string? sessionToken = null,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(cacheDir) || string.IsNullOrWhiteSpace(appDataDir) || webView is null)
            return false;

        try
        {
            var bootstrap = await _authService.BootstrapAsync(launchBrowser: false, cancellationToken).ConfigureAwait(true);
            if (bootstrap.LaunchedBrowser)
                CursorWebSyncLog.Write("bootstrap launched browser and collected dashboard session candidates");

            var candidates = BuildTokenCandidates(bootstrap, sessionToken);
            if (candidates.Count == 0)
            {
                return await TrySyncDashboardWithTokenAsync(
                        cacheDir,
                        appDataDir,
                        webView,
                        null,
                        "webview-session",
                        cancellationToken)
                    .ConfigureAwait(true);
            }

            foreach (var candidate in candidates)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var ok = await TrySyncDashboardWithTokenAsync(
                        cacheDir,
                        appDataDir,
                        webView,
                        candidate.Token,
                        candidate.Source,
                        cancellationToken)
                    .ConfigureAwait(true);
                if (ok)
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

    private static List<CursorDashboardTokenCandidate> BuildTokenCandidates(
        CursorDashboardBootstrapResult bootstrap,
        string? fallbackToken)
    {
        var results = new List<CursorDashboardTokenCandidate>();
        var seen = new HashSet<string>(StringComparer.Ordinal);
        void Add(string? token, string? source)
        {
            var normalized = CursorTokenNormalizer.Normalize(token);
            if (string.IsNullOrWhiteSpace(normalized) || !seen.Add(normalized))
                return;
            results.Add(new CursorDashboardTokenCandidate(normalized, source));
        }

        foreach (var candidate in bootstrap.Candidates)
            Add(candidate.Token, candidate.Source);
        Add(bootstrap.PrimaryToken, "bootstrap-primary");
        Add(fallbackToken, "resolver-fallback");
        return results;
    }

    private async Task<bool> TrySyncDashboardWithTokenAsync(
        string cacheDir,
        string appDataDir,
        CoreWebView2 webView,
        string? token,
        string? source,
        CancellationToken cancellationToken)
    {
        CursorWebSyncLog.Write($"dashboard sync try source={source ?? "unknown"}");
        var capture = new CursorDashboardCapture();
        var context = await OpenDashboardContextAsync(webView, token, capture, cancellationToken).ConfigureAwait(true);
        if (!context.Ready)
        {
            CursorWebSyncLog.Write(
                $"dashboard not ready source={source ?? "unknown"} href={context.Href ?? "(unknown)"} checkpoint={context.OnCheckpoint} auth={context.OnAuthPage} cookie={context.HasSessionCookie} note={context.Note ?? capture.LastNote ?? "unknown"}");
            return false;
        }

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
        if (events.Count > 0)
        {
            await WriteUsageJsonAsync(cacheDir, events, cancellationToken).ConfigureAwait(true);
            CursorWebSyncLog.Write($"usage-json ok events={events.Count} source={capture.LastNote ?? source ?? "same-origin-fetch"}");
            usageOk = true;
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

        var summaryOk = false;
        if (summary.ValueKind == JsonValueKind.Object)
        {
            Directory.CreateDirectory(appDataDir);
            var target = Path.Combine(appDataDir, WebUsageSummaryFileName);
            var json =
                "{\n  \"fetchedAt\": " + JsonSerializer.Serialize(DateTime.UtcNow.ToString("O")) +
                ",\n  \"summary\": " + summary.GetRawText() + "\n}\n";
            await WriteTextAtomicallyAsync(target, json, cancellationToken).ConfigureAwait(true);
            CursorWebSyncLog.Write("usage-summary ok");
            summaryOk = true;
        }
        else
        {
            CursorWebSyncLog.Write(
                $"usage-summary failed source={source ?? "unknown"} href={context.Href ?? "(unknown)"} note={capture.LastNote ?? context.Note ?? "empty"} auth={context.OnAuthPage} cookie={context.HasSessionCookie}");
        }

        return usageOk || summaryOk;
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
        CancellationToken cancellationToken)
    {
        if (!string.IsNullOrWhiteSpace(token))
            await InjectSessionCookiesAsync(webView, token).ConfigureAwait(true);

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(TimeSpan.FromSeconds(150));

        var navigationCompleted = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        void OnNavigationCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs args)
        {
            if (!args.IsSuccess)
                return;
            var source = webView.Source ?? string.Empty;
            if (source.Contains("cursor.com", StringComparison.OrdinalIgnoreCase))
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
            webView.Navigate(DashboardUsageUrl);
            using var _ = timeoutCts.Token.Register(() => navigationCompleted.TrySetResult(false));
            await navigationCompleted.Task.ConfigureAwait(true);

            var context = await WaitForDashboardContextAsync(webView, token, timeoutCts.Token).ConfigureAwait(true);
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
        string? injectedToken,
        CancellationToken cancellationToken)
    {
        var deadline = Environment.TickCount64 + CheckpointWaitMs;
        DashboardContext? last = null;
        var pollCounter = 0;
        while (Environment.TickCount64 < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            last = await ProbeDashboardPageAsync(webView).ConfigureAwait(true);
            if (last.Ready)
                return last;
            if (last.OnAuthPage && IsAuthenticatorRoute(last.Href))
            {
                return last with { Ready = false, Note = "authenticator redirect; try next session candidate" };
            }
            if (last.OnAuthPage && !last.HasSessionCookie && ++pollCounter % 3 == 0)
            {
                var polled = await PollBrowserSessionTokenAsync(cancellationToken).ConfigureAwait(true);
                if (!string.IsNullOrWhiteSpace(polled)
                    && !string.Equals(polled, injectedToken, StringComparison.Ordinal))
                {
                    injectedToken = polled;
                    await InjectSessionCookiesAsync(webView, polled).ConfigureAwait(true);
                    webView.Reload();
                    CursorWebSyncLog.Write("imported browser WorkosCursorSessionToken into WebView2");
                }
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
        var effectiveReady = ready || (onDashboardRoute && !checkpoint && !auth && hasCookie);
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
        var allEvents = new List<JsonElement>();
        int? totalCount = capture.TotalUsageEventsCount;
        var hasMore = true;
        for (var page = 1; page <= MaxPages && hasMore; page++)
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
                    'Referer': '{{DashboardUsageUrl}}'
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

    private async Task<string?> PollBrowserSessionTokenAsync(CancellationToken cancellationToken)
    {
        var bootstrap = await _authService.BootstrapAsync(launchBrowser: false, cancellationToken).ConfigureAwait(true);
        foreach (var candidate in bootstrap.Candidates)
        {
            if (string.Equals(candidate.Source, "browser-cookies", StringComparison.OrdinalIgnoreCase))
                return candidate.Token;
        }
        return bootstrap.PrimaryToken;
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

    private static async Task InjectSessionCookiesAsync(CoreWebView2 webView, string token)
    {
        var manager = webView.CookieManager;
        var expires = DateTime.UtcNow.AddDays(30);
        foreach (var domain in new[] { ".cursor.com", "cursor.com" })
        {
            var cookie = manager.CreateCookie("WorkosCursorSessionToken", token, domain, "/");
            cookie.IsSecure = true;
            cookie.IsHttpOnly = true;
            cookie.SameSite = CoreWebView2CookieSameSiteKind.None;
            cookie.Expires = expires;
            manager.AddOrUpdateCookie(cookie);
        }
        await Task.CompletedTask.ConfigureAwait(true);
    }

    private static async Task WriteUsageJsonAsync(
        string cacheDir,
        IReadOnlyList<JsonElement> events,
        CancellationToken cancellationToken)
    {
        Directory.CreateDirectory(cacheDir);
        var target = Path.Combine(cacheDir, UsageJsonFileName);
        await WriteTextAtomicallyAsync(target, BuildUsageJsonText(events), cancellationToken).ConfigureAwait(true);
    }

    private static string BuildUsageJsonText(IReadOnlyList<JsonElement> events)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions { Indented = true }))
        {
            writer.WriteStartObject();
            writer.WriteNumber("version", 1);
            writer.WriteString("source", "webview-json");
            writer.WriteString("syncedAt", DateTime.UtcNow.ToString("O"));
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
}
