using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using SidecarWinUI.Services;
using System.Net.Http.Json;
using System.Text.Json;
using Windows.Storage.Pickers;
using Windows.Storage;
using WinRT;

namespace SidecarWinUI.Pages;

public sealed partial class ArchivistPage : Page
{
    private readonly HttpClient _client;
    private string? _lastUploadPath;

    public ArchivistPage()
    {
        InitializeComponent();
        BackendService.Instance.EnsureBackendStarted();
        _client = BackendService.Instance.Client;
        _client.BaseAddress = new Uri(BackendService.Instance.BaseUrl);
    }

    private async void OnLookupAlbum(object sender, RoutedEventArgs e)
    {
        var query = AlbumQueryBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(query))
        {
            return;
        }
        var response = await _client.GetFromJsonAsync<List<Dictionary<string, object>>>(
            $"/api/archivist/album-info?q={Uri.EscapeDataString(query)}");
        AlbumResults.ItemsSource = response?.Select(row =>
            $"{row.GetValueOrDefault("artist", "-")}" +
            $" - {row.GetValueOrDefault("title", row.GetValueOrDefault("album", ""))}" +
            $" ({row.GetValueOrDefault("catalog_number", "n/a")})");
    }

    private async void OnUploadRip(object sender, RoutedEventArgs e)
    {
        var picker = new FileOpenPicker();
        picker.FileTypeFilter.Add(".mp3");
        picker.FileTypeFilter.Add(".wav");
        picker.FileTypeFilter.Add(".flac");
        picker.FileTypeFilter.Add(".m4a");
        picker.FileTypeFilter.Add(".ogg");
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(App.Current.Windows[0]);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
        StorageFile? file = await picker.PickSingleFileAsync();
        if (file is null)
        {
            return;
        }

        UploadStatus.Text = "Uploading...";
        using var form = new MultipartFormDataContent();
        using var stream = await file.OpenStreamForReadAsync();
        form.Add(new StreamContent(stream), "rip_file", file.Name);
        var result = await _client.PostAsync("/api/archivist/album-rip/upload", form);
        var body = await result.Content.ReadAsStringAsync();
        if (result.IsSuccessStatusCode)
        {
            UploadStatus.Text = $"Uploaded {file.Name}";
            _lastUploadPath = file.Path;
        }
        else
        {
            UploadStatus.Text = "Upload failed";
        }
    }

    private async void OnAnalyzeRip(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_lastUploadPath))
        {
            UploadStatus.Text = "Upload a rip first.";
            return;
        }
        var payload = new
        {
            silence_thresh_db = int.TryParse(SilenceBox.Text, out var s) ? s : -38,
            min_gap_ms = int.TryParse(GapBox.Text, out var g) ? g : 1200,
            min_track_ms = int.TryParse(TrackBox.Text, out var t) ? t : 60000,
        };
        var response = await _client.PostAsJsonAsync("/api/archivist/album-rip", payload);
        var body = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode)
        {
            UploadStatus.Text = "Analysis failed.";
            return;
        }
        var doc = JsonDocument.Parse(body);
        if (!doc.RootElement.TryGetProperty("segments", out var segments))
        {
            return;
        }
        RipSegments.ItemsSource = segments.EnumerateArray().Select(seg =>
            $"{seg.GetProperty("start_ms").GetInt64()} → {seg.GetProperty("end_ms").GetInt64()} ({seg.GetProperty("duration_ms").GetInt64()} ms)");
    }

    private async void OnLookupMb(object sender, RoutedEventArgs e)
    {
        var title = MbTitleBox.Text.Trim();
        var artist = MbArtistBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(title) && string.IsNullOrWhiteSpace(artist))
        {
            return;
        }
        var response = await _client.GetStringAsync($"/api/archivist/musicbrainz-releases?title={Uri.EscapeDataString(title)}&artist={Uri.EscapeDataString(artist)}");
        var doc = JsonDocument.Parse(response);
        var results = doc.RootElement.GetProperty("results");
        MbReleaseCombo.Items.Clear();
        foreach (var set in results.EnumerateArray())
        {
            if (!set.TryGetProperty("releases", out var releases))
            {
                continue;
            }
            foreach (var rel in releases.EnumerateArray())
            {
                var label = $"{set.GetProperty("title").GetString()} — {set.GetProperty("artist").GetString()}";
                var display = $"{label} [{rel.GetProperty("format").GetString()} | {rel.GetProperty("date").GetString()}]";
                MbReleaseCombo.Items.Add(new ComboBoxItem { Content = display, Tag = rel.ToString() });
            }
        }
    }

    private void OnReleaseChanged(object sender, SelectionChangedEventArgs e)
    {
        if (MbReleaseCombo.SelectedItem is not ComboBoxItem item || item.Tag is null)
        {
            return;
        }
        var relDoc = JsonDocument.Parse(item.Tag.ToString() ?? "{}");
        if (!relDoc.RootElement.TryGetProperty("tracks", out var tracks))
        {
            MbTracks.ItemsSource = Array.Empty<string>();
            return;
        }
        MbTracks.ItemsSource = tracks.EnumerateArray().Select(track =>
            $"{track.GetProperty("title").GetString()} ({track.GetProperty("length_ms").GetInt64()} ms)");
    }

    private async void OnSearch(object sender, RoutedEventArgs e)
    {
        var query = SearchBox.Text.Trim();
        var showAll = ShowAllToggle.IsChecked == true ? "1" : "0";
        var response = await _client.GetStringAsync($"/sidecar-api/archivist/search?q={Uri.EscapeDataString(query)}&show_all={showAll}");
        var doc = JsonDocument.Parse(response);
        var results = doc.RootElement.GetProperty("results");
        SearchResults.ItemsSource = results.EnumerateArray().Select(row =>
            $"{row.GetProperty("artist").GetString()} - {row.GetProperty("title").GetString()} ({row.GetProperty("catalog_number").GetString()})");
    }
}
