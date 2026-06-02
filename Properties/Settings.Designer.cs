//------------------------------------------------------------------------------
// dual-stack: properties settings designer
//------------------------------------------------------------------------------

namespace AgentStatistics.Properties;

/// <summary>
/// 应用程序设置（设计期生成物的最小手写替代）。
/// </summary>
public sealed partial class Settings : global::System.Configuration.ApplicationSettingsBase
{
    private static readonly Settings DefaultInstance = new();

    /// <summary>默认设置实例。</summary>
    public static Settings Default => DefaultInstance;

    /// <summary>界面显示名称。</summary>
    [global::System.Configuration.ApplicationScopedSettingAttribute()]
    [global::System.Diagnostics.DebuggerNonUserCodeAttribute()]
    [global::System.Configuration.DefaultSettingValueAttribute("Agent Statistics")]
    public string AppDisplayName
    {
        get => ((string)(this["AppDisplayName"]));
        set => this["AppDisplayName"] = value;
    }
}
