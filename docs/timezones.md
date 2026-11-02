# Station time zones

In Settings, select **Station Time Zone → ET — Eastern (EST/EDT)** for WLMC
and save once. RAMS then follows Eastern daylight saving rules automatically;
no spring or autumn settings change is needed. PT — Pacific (PST/PDT), CT,
MT, Alaska, Atlantic, Newfoundland, London, Arizona and Hawaii are also available.
Arizona and Hawaii choices stay on standard time all year.

RAMS stores regional IANA identifiers (for example `America/New_York`), so
scheduled shows keep their local clock time across seasonal changes. Dated
timestamps show the abbreviation appropriate to that record's date, such as
EST in winter and EDT in summer. JavaScript and calendar/API data retain the
IANA identifier so clients can apply the same rules.

Existing explicit UTC offsets remain fixed for compatibility. A setting of
UTC-5 or Etc/GMT+5 cannot identify a region; select ET once to enable automatic
switching. Existing regional identifiers, including ones outside the dropdown's
presets, remain selected and are preserved when settings are saved.

Configuration abbreviations ET/EST/EDT, CT/CST/CDT, MT/MST/MDT and PT/PST/PDT
are interpreted as their respective US regions with seasonal switching. Use an
explicit UTC offset for a fixed time zone, or America/Phoenix for Arizona.
New installations and empty settings default to America/New_York.

Saving a different zone refreshes future show triggers immediately, without
restarting RAMS or stopping active recordings. Nonexistent or repeated clock
times during the transition hour follow APScheduler's existing regional cron
behavior; this change does not add a new policy for those unusual show slots.
