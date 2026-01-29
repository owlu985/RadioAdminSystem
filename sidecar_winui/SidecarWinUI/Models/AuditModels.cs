namespace SidecarWinUI.Models;

public sealed record AuditJob(string JobId);

public sealed record AuditStatus(
    string? Status,
    int? Progress,
    int? Total,
    List<Dictionary<string, object>>? Results,
    string? Error
);

public sealed record AuditRun(
    int Id,
    string Action,
    string Status,
    string CreatedAt,
    string? CompletedAt
);
