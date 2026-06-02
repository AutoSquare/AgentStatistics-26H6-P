using System.IO;
using System.Runtime.InteropServices;

namespace AgentStatistics.Services;

/// <summary>
/// 应用程序内容根目录及双栈子路径：脚本目录与 Python 环境。
/// </summary>
public static class AppPaths
{
    /// <summary>应用程序根目录（可执行文件所在目录）。</summary>
    public static string Root { get; } = AppDomain.CurrentDomain.BaseDirectory;

    /// <summary>Python 脚本目录。</summary>
    public static string PyFolder => Path.Combine(Root, "ASPy");

    /// <summary>Python 环境目录名（项目内 {Abbr}Env）。</summary>
    public static string EnvFolderName => "ASEnv";

    /// <summary>
    /// Python 解释器路径。优先 venv 布局（Windows <c>Scripts/python.exe</c>），
    /// 其次嵌入式布局（<c>python.exe</c> 位于环境根），最后 Unix <c>bin/python</c>。
    /// </summary>
    public static string PythonExe => ResolvePythonExecutable(Root, EnvFolderName);

    /// <summary>临时工作区名前缀。</summary>
    public static string WorkspacePrefix => "ASWork_";

    /// <summary>
    /// 按常见部署布局解析 Python 可执行文件路径。
    /// </summary>
    /// <param name="root">应用程序根目录。</param>
    /// <param name="envFolder">环境目录名。</param>
    /// <returns>首个存在的候选路径；均不存在时返回 Windows venv 默认路径供错误提示。</returns>
    public static string ResolvePythonExecutable(string root, string envFolder)
    {
        var envRoot = Path.Combine(root, envFolder);
        var candidates = RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
            ? new[]
            {
                Path.Combine(envRoot, "Scripts", "python.exe"),
                Path.Combine(envRoot, "python.exe"),
                Path.Combine(envRoot, "bin", "python"),
            }
            : new[]
            {
                Path.Combine(envRoot, "bin", "python"),
                Path.Combine(envRoot, "python.exe"),
                Path.Combine(envRoot, "Scripts", "python.exe"),
            };
        foreach (var path in candidates)
        {
            if (File.Exists(path))
                return path;
        }
        return candidates[0];
    }
}
