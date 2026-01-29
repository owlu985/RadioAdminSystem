namespace SidecarWinUI.Models;

public sealed record ArchivistRow(
    string? Artist,
    string? Title,
    string? Album,
    string? CatalogNumber,
    string? PriceRange,
    string? Notes
);

public sealed record ArchivistRipResult(
    int Id,
    string? Filename,
    long DurationMs,
    string? SettingsJson,
    List<ArchivistSegment> Segments
);

public sealed record ArchivistSegment(long StartMs, long EndMs, long DurationMs);

public sealed record MusicbrainzReleaseSet(string? Title, string? Artist, List<MusicbrainzRelease> Releases);

public sealed record MusicbrainzRelease(string Id, string? Format, string? Date, List<MusicbrainzTrack> Tracks);

public sealed record MusicbrainzTrack(string? Title, long? LengthMs, string? Format);
