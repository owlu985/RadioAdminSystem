using Microsoft.UI.Xaml.Controls;
using SidecarWinUI.Services;

namespace SidecarWinUI.Pages;

public sealed partial class ShowAutomatorPage : Page
{
    public ShowAutomatorPage()
    {
        InitializeComponent();
        BackendService.Instance.EnsureBackendStarted();
        ShowWebView.Source = new Uri($"{BackendService.Instance.BaseUrl}/dj/show-automator");
    }
}
