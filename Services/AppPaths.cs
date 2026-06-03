using System.IO;
using System.Runtime.InteropServices;

namespace AgentStatistics.Services;

/// <summary>
/// Application content paths for scripts and the portable Python environment.
/// </summary>
public static class AppPaths
{
    /// <summary>Application root directory, normally the executable output folder.</summary>
    public static string Root { get; } = AppDomain.CurrentDomain.BaseDirectory;

    /// <summary>Python script directory.</summary>
    public static string PyFolder => Path.Combine(Root, "ASPy");

    /// <summary>Portable Python environment folder. This is packaged with the app.</summary>
    public static string EnvFolderName => "ASEnv";

    /// <summary>Portable Python environment path.</summary>
    public static string EnvFolder => Path.Combine(Root, EnvFolderName);

    /// <summary>Python executable path inside the portable environment.</summary>
    public static string PythonExe => ResolvePythonExecutable(Root, EnvFolderName);

    /// <summary>Temporary workspace folder prefix.</summary>
    public static string WorkspacePrefix => "ASWork_";

    public static string ResolvePythonExecutable(string root, string envFolder)
    {
        var envRoot = Path.Combine(root, envFolder);
        var candidates = RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
            ? new[]
            {
                Path.Combine(envRoot, "python.exe"),
                Path.Combine(envRoot, "Scripts", "python.exe"),
                Path.Combine(envRoot, "bin", "python"),
            }
            : new[]
            {
                Path.Combine(envRoot, "bin", "python"),
                Path.Combine(envRoot, "python"),
                Path.Combine(envRoot, "python.exe"),
            };
        foreach (var path in candidates)
        {
            if (File.Exists(path))
                return path;
        }
        return candidates[0];
    }
}
