# Airing audit and absence records

The audit groups evidence by show and the station-local date the airing started.
Log submission time is not an airing date. Linked show runs are converted from
UTC to the station timezone. Overnight recovery timestamps are matched to the
scheduled start date. New log/recording sidecars retain `airing_date` and show IDs.

Existing log dates are reconciled in the report when a next-day date has no
scheduled airing and the previous midnight/overnight window fits the log times.
The original CSV remains untouched. Daily shows with two plausible dates require
an explicit airing selection rather than a guessed correction.

A full recording and its recovery parts count as one airing. An empty original
can be supported by nonempty parts, including `_part141`. Without an original,
the existing rule remains: a single orphaned part counts; multiple orphaned parts
need a digital log. The Proof / reason disclosure explains the evidence, links a
recording, and identifies a conflicting Missed flag.

## Actual / possible totals

Possible means scheduled slots that have finished so far, within the show's date
range and selected period boundaries. Upcoming and in-progress shows are excluded.
Approved absences stay in the denominator. Set period boundaries using the audit's
Set dates control (requires settings permission); normal settings saves preserve
these dates. Until boundaries are set, totals use the saved show date ranges and
the page displays that limitation. Missing historical schedules produce `—`, not
an invented denominator. Schedule edits still affect historical expectations.

A substitute gets actual attendance credit only when airing evidence exists and
the airing is not flagged Missed. The absent DJ keeps their scheduled opportunity
in the denominator but gets no actual credit for that absence. A substitute's
accepted past cover is also an opportunity in their denominator. A co-host who
aired keeps their credit. Booking a substitute by itself is not proof of airing.

## Calendar codes

| Code | Meaning |
| --- | --- |
| A | Aired |
| E | Recorded approved/resolved absence |
| E/A | Recorded absence, another co-host aired |
| S | Substitute aired, supported by evidence |
| E/S | Absence with substitute arranged; airing not confirmed |
| P, A/P | Pending absence; optionally with airing evidence |
| M | Flagged Missed with no recorded notification |
| ? | No qualifying evidence; attendance is unknown |
| U | Upcoming or in progress |

Approved/resolved absence records are distinct from Missed flags. Rejected requests
do not excuse an airing. Pending requests are shown separately. A missed substitute
airing stays excluded even if cover was arranged. An absence reason is shown only
on the authenticated management/audit pages.

## Retrospective absence entry

Use Absence Queue -> Add Absence. Choose the DJ, show, airing start date, optional
substitute, status and reason. Dates must match the show's recurring weekday and
saved date range; overnight endings carry into the next day automatically. Past
dates are allowed for managers. Existing entries can have their date/reason/cover
corrected, and duplicate entries for the same DJ and airing are refused. Historical
edits do not refresh or stop the live recorder. The public absence form continues
to accept future dates for approval.

## Further QoL ideas (not implemented in this change)

1. Preserve an immutable schedule snapshot per airing so later show edits cannot
   rewrite historical denominators or host assignments.
2. Add a Needs review inbox for evidence/flag conflicts, uncertain dates, missing
   logs and missing historical schedules, with direct correction links.
3. Warn when a recording generates an unusually high number of parts, with its
   recorder/network failure history attached; this is separate from DJ attendance.
4. Offer an export containing attendance codes, evidence sources, reasons and the
   period/date-range assumptions used to calculate each ratio.
5. Add an admin activity history for attendance corrections, including who changed
   the status, when, and why.
