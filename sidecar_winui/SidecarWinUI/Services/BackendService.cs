using System.Diagnostics;
using System.Net.Http;

namespace SidecarWinUI.Services;

public sealed class BackendService
{
    private static readonly Lazy<BackendService> Lazy = new(() => new BackendService());
    private Process? _backend;
    public static BackendService Instance => Lazy.Value;

    public string BaseUrl { get; } = "http://127.0.0.1:5055";
    public HttpClient Client { get; } = new();

    private BackendService()
    {
        Client.Timeout = TimeSpan.FromSeconds(30);
    }

    public void EnsureBackendStarted()
    {
        if (_backend is { HasExited: false })
        {
            return;
        }

        var repoRoot = AppContext.BaseDirectory;
        var backendPath = Path.GetFullPath(Path.Combine(repoRoot, "..", "..", "..", "..", "sidecar", "app.py"));
        if (!File.Exists(backendPath))
        {
            return;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = "python",
            Arguments = $"\"{backendPath}\"",
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        _backend = Process.Start(startInfo);
    }
}
