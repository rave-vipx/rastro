# Engineering decisions

Architecture decision records (ADRs) explain the tradeoffs behind Rastro's design. Each record
states the context, the options considered, the decision and its consequences. Records are
numbered in the order they were accepted; numbers 0027 to 0029 were never used. A later record
that changes an earlier one says so in its status line, and the earlier record is kept as written
so the history stays readable.

The newest records come first in the navigation.

Recent records:

| Record | Decision |
| --- | --- |
| [0040](0040-crawl-checkpoint.md) | Unbounded crawls keep a JSON checkpoint in Rastro's own frontier. |
| [0039](0039-acquisition-ladder.md) | A blocked source climbs an acquisition ladder, ending in operator solver plugins. |
| [0038](0038-http-transport.md) | Keep Rastro's pinned HTTP transport and add impersonation and HTTP/3 to it. |
| [0037](0037-input-origin-and-capabilities.md) | Input origin decides which capabilities a job may use. |
| [0036](0036-scrapling-access-and-unbounded-crawl.md) | Capability first: use everything the acquisition engine can do. |
