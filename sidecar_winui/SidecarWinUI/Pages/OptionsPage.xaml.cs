using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using SidecarWinUI.Services;
using System.Net.Http.Json;
using System.Text.Json;

namespace SidecarWinUI.Pages;

public sealed partial class OptionsPage : Page
{
    private readonly HttpClient _client;

    public OptionsPage()
    {
        InitializeComponent();
        BackendService.Instance.EnsureBackendStarted();
        _client = BackendService.Instance.Client;
        _client.BaseAddress = new Uri(BackendService.Instance.BaseUrl);
        _ = LoadOptions();
    }

    private async Task LoadOptions()
    {
        var response = await _client.GetStringAsync("/sidecar-api/options");
        var doc = JsonDocument.Parse(response);
        MusicRootBox.Text = doc.RootElement.GetProperty("NAS_MUSIC_ROOT").GetString() ?? string.Empty;
        SpreadsheetBox.Text = doc.RootElement.GetProperty("MONEYMUSIC_SPREADSHEET_PATH").GetString() ?? string.Empty;
    }

    private async void OnSave(object sender, RoutedEventArgs e)
    {
        var payload = new
        {
            NAS_MUSIC_ROOT = MusicRootBox.Text.Trim(),
            MONEYMUSIC_SPREADSHEET_PATH = SpreadsheetBox.Text.Trim(),
        };
        var response = await _client.PostAsJsonAsync("/sidecar-api/options", payload);
        StatusText.Text = response.IsSuccessStatusCode ? "Saved" : "Save failed";
    }
}
