//------------------------------------------------------------------------------
// dual-stack: properties resource designer
//------------------------------------------------------------------------------

#nullable enable

namespace AgentStatistics.Properties;

using System.Resources;

/// <summary>
/// 强类型资源访问（最小壳，可按需扩展 Resource.resx）。
/// </summary>
public class Resource
{
    private static ResourceManager? _resourceMan;

    /// <summary>缓存的 ResourceManager。</summary>
    public static ResourceManager ResourceManager =>
        _resourceMan ??= new ResourceManager("AgentStatistics.Properties.Resource", typeof(Resource).Assembly);
}
