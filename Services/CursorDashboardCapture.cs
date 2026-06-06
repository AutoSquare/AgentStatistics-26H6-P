using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;

namespace AgentStatistics.Services;

/// <summary>
/// 收集 Cursor Dashboard 页面自身发起的 API 响应，避免手写 fetch 与官网行为不一致。
/// </summary>
internal sealed class CursorDashboardCapture
{
    private readonly ConcurrentDictionary<string, byte> _seenUrls = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>拦截到的 usage-summary JSON 根对象。</summary>
    public JsonElement? UsageSummary { get; private set; }

    /// <summary>拦截到的用量事件。</summary>
    public List<JsonElement> Events { get; } = [];

    /// <summary>官网返回的总事件数（若响应中包含）。</summary>
    public int? TotalUsageEventsCount { get; private set; }

    /// <summary>最近一次诊断信息。</summary>
    public string? LastNote { get; set; }

    /// <summary>页面最终 URL。</summary>
    public string? PageHref { get; set; }

    /// <summary>是否检测到登录/鉴权页。</summary>
    public bool SawAuthPage { get; set; }

    /// <summary>
    /// 尝试解析并吸收 Dashboard 相关 API 响应体。
    /// </summary>
    /// <param name="requestUri">请求 URI。</param>
    /// <param name="statusCode">HTTP 状态码。</param>
    /// <param name="body">响应正文。</param>
    public void TryAbsorbResponse(string? requestUri, int statusCode, string body)
    {
        if (string.IsNullOrWhiteSpace(requestUri) || string.IsNullOrWhiteSpace(body))
            return;
        if (!_seenUrls.TryAdd($"{requestUri}:{statusCode}:{body.Length}", 0))
            return;

        if (requestUri.Contains("/api/usage-summary", StringComparison.OrdinalIgnoreCase))
        {
            if (statusCode is >= 200 and < 300 && TryParseObject(body, out var summary))
            {
                UsageSummary = summary;
                LastNote = "captured usage-summary";
            }
            else
            {
                LastNote = $"usage-summary status={statusCode} body={TrimBody(body)}";
            }
            return;
        }

        if (!requestUri.Contains("/api/dashboard/get-filtered-usage-events", StringComparison.OrdinalIgnoreCase))
            return;

        if (statusCode is < 200 or >= 300)
        {
            LastNote = $"usage-events status={statusCode} body={TrimBody(body)}";
            return;
        }

        if (!TryParseObject(body, out var payload))
        {
            LastNote = "usage-events response not json object";
            return;
        }

        if (payload.TryGetProperty("totalUsageEventsCount", out var totalElement))
        {
            if (totalElement.ValueKind == JsonValueKind.Number && totalElement.TryGetInt32(out var totalNumber))
                TotalUsageEventsCount = totalNumber;
            else if (totalElement.ValueKind == JsonValueKind.String
                     && int.TryParse(totalElement.GetString(), out var parsed))
                TotalUsageEventsCount = parsed;
        }

        var batch = ExtractUsageEvents(payload);
        if (batch.Count == 0)
        {
            LastNote = "usage-events captured empty batch";
            return;
        }

        Events.AddRange(batch);
        LastNote = $"captured usage-events batch={batch.Count} total={TotalUsageEventsCount?.ToString() ?? "?"}";
    }

    private static bool TryParseObject(string body, out JsonElement root)
    {
        root = default;
        try
        {
            using var document = JsonDocument.Parse(body);
            if (document.RootElement.ValueKind != JsonValueKind.Object)
                return false;
            root = document.RootElement.Clone();
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
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

    private static string TrimBody(string body)
    {
        var text = body.Replace('\n', ' ').Replace('\r', ' ').Trim();
        return text.Length <= 160 ? text : text[..160];
    }
}
