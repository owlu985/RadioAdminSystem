using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using SidecarWinUI.Services;
using System.Net.Http.Json;
using System.Text.Json;

namespace SidecarWinUI.Pages;

public sealed partial class AuditPage : Page
{
    private readonly HttpClient _client;

    public AuditPage()
    {
        InitializeComponent();
        BackendService.Instance.EnsureBackendStarted();
        _client = BackendService.Instance.Client;
        _client.BaseAddress = new Uri(BackendService.Instance.BaseUrl);
        _ = LoadRuns();
    }

    private async Task<string?> StartAudit(string action, object payload)
    {
        var response = await _client.PostAsJsonAsync("/api/audit/start", new Dictionary<string, object>
        {
            ["action"] = action,
            ["params"] = payload,
        });
        var body = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode)
        {
            return null;
        }
        var doc = JsonDocument.Parse(body);
        return doc.RootElement.TryGetProperty("job_id", out var id) ? id.GetString() : null;
    }

    private async Task PollAudit(string jobId, ProgressBar bar, TextBlock status, ListView target, string action)
    {
        while (true)
        {
            var response = await _client.GetStringAsync($"/api/audit/status/{jobId}");
            var doc = JsonDocument.Parse(response);
            var state = doc.RootElement.GetProperty("status").GetString();
            var total = doc.RootElement.TryGetProperty("total", out var totalProp) ? totalProp.GetInt32() : 1;
            var progress = doc.RootElement.TryGetProperty("progress", out var progProp) ? progProp.GetInt32() : 0;
            var percent = total > 0 ? (progress * 100.0 / total) : 0;
            bar.Value = percent;
            status.Text = $"{state} {progress}/{total}";

            if (state is "completed" or "error")
            {
                if (doc.RootElement.TryGetProperty("results", out var results))
                {
                    target.ItemsSource = results.EnumerateArray().Select(item => item.ToString());
                }
                await LoadRuns();
                break;
            }
            await Task.Delay(2000);
        }
    }

    private async void OnRunRecordingsAudit(object sender, RoutedEventArgs e)
    {
        var folder = RecordingsFolderBox.Text.Trim();
        var jobId = await StartAudit("recordings", new { folder = string.IsNullOrWhiteSpace(folder) ? null : folder });
        if (jobId is null)
        {
            RecordingsStatus.Text = "Failed to start";
            return;
        }
        _ = PollAudit(jobId, RecordingsProgress, RecordingsStatus, RecordingsResults, "recordings");
    }

    private async void OnRunExplicitAudit(object sender, RoutedEventArgs e)
    {
        var rate = double.TryParse(RateLimitBox.Text, out var r) ? r : 3.1;
        var maxFiles = int.TryParse(MaxFilesBox.Text, out var m) ? m : 500;
        var lyrics = LyricsCheckBox.IsChecked == true;
        var jobId = await StartAudit("explicit", new { rate, max_files = maxFiles, lyrics_check = lyrics });
        if (jobId is null)
        {
            ExplicitStatus.Text = "Failed to start";
            return;
        }
        _ = PollAudit(jobId, ExplicitProgress, ExplicitStatus, ExplicitResults, "explicit");
    }

    private async Task LoadRuns()
    {
        var response = await _client.GetStringAsync("/api/audit/runs?limit=20");
        var doc = JsonDocument.Parse(response);
        AuditRuns.ItemsSource = doc.RootElement.EnumerateArray().Select(run =>
            $"{run.GetProperty("id").GetInt32()} - {run.GetProperty("action").GetString()} ({run.GetProperty("status").GetString()})");
    }

    private async void OnRefreshRuns(object sender, RoutedEventArgs e)
    {
        await LoadRuns();
    }
}
