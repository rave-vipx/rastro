# Daily dataset and agenda (SCHED-007, DATA-004)

## GitHub Actions (recommended)

The `Documentation` workflow (`.github/workflows/docs.yml`) refreshes the Uruguay dance
agenda every day at 11:00 UTC (08:00 in Montevideo), with no machine of yours involved.
After building the docs it restores the previous `agenda.sqlite` from the Actions cache,
runs `rastro job load` on `examples/uy-dance-agenda/job.toml`, exports the upcoming events
as HTML to `agenda/index.html`, saves the dataset back to the cache and deploys the site.
The refresh can never fail the docs deploy.

- Page: <https://rave-vipx.github.io/rastro/agenda/> (the page states when it was generated).
- Evidence: each run uploads the `agenda-evidence` artifact (load log with the exit code,
  `rastro dataset status` output) for 30 days, and writes the exit code and source status
  to the run summary. Exit 0 is success, 1 failure, 2 configuration error, 3 partial
  success. A stale source (`stale_sources` in the status) shows that a site changed or
  blocked the request; its previous rows stay in the page.
- Scope: scheduled workflows only run from the default branch, so the refresh starts once
  the workflow is on `main`. You can also start it by hand with "Run workflow".
- Limits: the cache can be evicted (GitHub drops entries unused for 7 days), in which case
  the dataset restarts empty and loses history; some sites may block GitHub runner IPs,
  which appears as a stale source.

## Local alternative

Use the same job for each run. These examples run at 08:00 local time; replace
absolute paths and the `rastro` executable path with your own. Both save the
load report, its exit code, and export errors to a log. Exit 0 means success,
1 means failure, 2 means configuration error, and 3 means partial success:
the load updates what it can, so the agenda is still exported. Inspect the log
and `rastro dataset status /absolute/path/agenda.sqlite` for stale sources.

Crontab (`crontab -e`):

```cron
0 8 * * * cd /absolute/path && /bin/sh -c '/absolute/path/to/rastro job load job.toml; rc=$?; echo "load exit: $rc"; [ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] && /absolute/path/to/rastro dataset export agenda.sqlite --format html --sort-field starts_on --upcoming --timezone America/Montevideo > agenda.html' >> /absolute/path/rastro.log 2>&1
```

macOS launchd (`~/Library/LaunchAgents/org.example.rastro.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>org.example.rastro</string>
  <key>WorkingDirectory</key><string>/absolute/path</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>-c</string>
    <string>/absolute/path/to/rastro job load job.toml; rc=$?; echo "load exit: $rc"; [ "$rc" -eq 0 ] || [ "$rc" -eq 3 ] &amp;&amp; /absolute/path/to/rastro dataset export agenda.sqlite --format html --sort-field starts_on --upcoming --timezone America/Montevideo &gt; agenda.html</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/absolute/path/rastro.log</string>
  <key>StandardErrorPath</key><string>/absolute/path/rastro-error.log</string>
</dict></plist>
```

Load it with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.example.rastro.plist`.
The HTML table includes the union of observed field names in first-seen order;
the dataset does not retain declared schema order or fields never observed.
`--upcoming` requires an ISO date or datetime `--sort-field` (`starts_on` for sources that
only publish a day); other values, such as integers, are a configuration error and missing
dates are excluded. A date is compared with today in `--timezone` (default UTC; the dataset
does not store its region, so pass the job's, e.g. `America/Montevideo`); datetimes without
an offset are read in that timezone too.
