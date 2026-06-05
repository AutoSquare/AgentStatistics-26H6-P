using System.Text.RegularExpressions;

namespace AgentStatistics.Services;

/// <summary>
/// Cursor Session Token 规范化工具，对齐 token-monitor 的手动 Cookie 粘贴规则。
/// </summary>
public static partial class CursorTokenNormalizer
{
    private const string CookieName = "WorkosCursorSessionToken";

    /// <summary>
    /// 将用户粘贴的 Token 或 Cookie 头规范化为裸 Session Token。
    /// </summary>
    /// <param name="input">用户输入，可为裸 Token、Cookie 名=值 或带 Cookie: 前缀的整段文本。</param>
    /// <returns>规范化后的 Token；无效输入返回 null。</returns>
    public static string? Normalize(string? input)
    {
        if (string.IsNullOrWhiteSpace(input))
            return null;

        var text = input.Trim();
        if (text.StartsWith("cookie:", StringComparison.OrdinalIgnoreCase))
            text = text[7..].Trim();

        var match = CookieValueRegex().Match(text);
        if (match.Success)
            text = match.Groups[1].Value.Trim();

        if (string.Equals(text, CookieName, StringComparison.OrdinalIgnoreCase))
            return null;
        if (text.Any(char.IsWhiteSpace))
            return null;
        if (text.Length < 8)
            return null;

        return text;
    }

    /// <summary>
    /// 从规范化 Token 推导账号标识，与 Python cursor_sync.derive_account_id 行为一致。
    /// </summary>
    /// <param name="token">已规范化的 Session Token。</param>
    /// <returns>账号 ID 字符串。</returns>
    public static string DeriveAccountId(string token)
    {
        if (token.Contains("%3A%3A", StringComparison.Ordinal))
        {
            var head = token.Split("%3A%3A", 2)[0].Trim();
            if (!string.IsNullOrEmpty(head))
                return head;
        }
        if (token.Contains("::", StringComparison.Ordinal))
        {
            var head = token.Split("::", 2)[0].Trim();
            if (!string.IsNullOrEmpty(head))
                return head;
        }
        var digest = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(token))).ToLowerInvariant();
        return "anon-" + digest[..12];
    }

    [GeneratedRegex(@"WorkosCursorSessionToken=([^;\s]+)", RegexOptions.IgnoreCase)]
    private static partial Regex CookieValueRegex();
}
