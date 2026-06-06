using System.IO;
using System.Text;

namespace AgentStatistics.Services;

/// <summary>
/// 将 Cursor WebView2 同步诊断写入 AppData 日志文件。
/// </summary>
internal static class CursorWebSyncLog
{
    private static readonly object Gate = new();

    /// <summary>
    /// 追加一行带 UTC 时间戳的同步日志。
    /// </summary>
    /// <param name="message">日志正文。</param>
    public static void Write(string message)
    {
        if (string.IsNullOrWhiteSpace(message))
            return;
        try
        {
            Directory.CreateDirectory(UserSettingsStore.AppDataDirectory);
            var path = Path.Combine(UserSettingsStore.AppDataDirectory, "cursor_web_sync.log");
            var line = $"{DateTime.UtcNow:O} {message.Trim()}{Environment.NewLine}";
            lock (Gate)
            {
                File.AppendAllText(path, line, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            }
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }
}
