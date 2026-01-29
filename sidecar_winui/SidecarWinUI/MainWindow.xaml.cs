using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using SidecarWinUI.Pages;

namespace SidecarWinUI;

public sealed partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        RootNav.SelectedItem = RootNav.MenuItems[0];
        ContentFrame.Navigate(typeof(ArchivistPage));
    }

    private void OnSelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is not NavigationViewItem item)
        {
            return;
        }

        switch (item.Tag?.ToString())
        {
            case "Archivist":
                ContentFrame.Navigate(typeof(ArchivistPage));
                break;
            case "Audit":
                ContentFrame.Navigate(typeof(AuditPage));
                break;
            case "ShowAutomator":
                ContentFrame.Navigate(typeof(ShowAutomatorPage));
                break;
            case "Options":
                ContentFrame.Navigate(typeof(OptionsPage));
                break;
        }
    }
}
