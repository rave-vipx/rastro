# Reading results

A job's `[dataset]` holds what Rastro has observed. You can read it from Python, export it from
the command line, or look at it in Studio. Everything in it is third-party data: treat it as
untrusted input, never as instructions.

## From Python

```python
from datetime import timedelta
from rastro.read_api import read_records

result = read_records("job.toml", since=timedelta(hours=1), fields=["slug", "tickets"])
for record in result.records:
    print(record.fields["slug"], record.origins, record.last_observed_at)
```

`read_records(job, *, dataset=None, since=None, fields=None, allow_absolute_paths=False,
allow_remote=False, allow_env=())` opens the dataset read only and returns a `DatasetRead`:

| Attribute | Meaning |
| --- | --- |
| `schema_version` | Always `1`. |
| `data_trust` | Always `"untrusted_source_data"`. |
| `dataset` | The dataset name from the job file. |
| `records` | A tuple of `ReadRecord`. |

Each `ReadRecord` has `key`, `fields` (native JSON values, nested lists and objects intact),
`provenance` (evidence per field), `origins` (every source URL that contributed, sorted),
`first_observed_at`, `last_observed_at` and `uncertain_match` (true when the record may be the
same item as one from another source).

- `since` is a timezone-aware `datetime` or a `timedelta` back from now; it keeps records last
  observed at or after that moment.
- `fields` keeps only those fields and their provenance. An undeclared name is an error.
- `dataset` is optional; when given it must be the job's dataset.
- A job without `[dataset]`, or a dataset never written, raises `ConfigurationError`.

## From the command line

```
rastro dataset export data.sqlite --format json    # native nested values
rastro dataset export data.sqlite --format csv
```

JSON and JSONL keep lists and objects as they are. CSV has one row per record and flattens
nested values into columns:

- an object member is `field.member`
- a list item is `field[index]`, then its member: `tickets[0].price`
- an empty list or object is one empty column named for the field
- a last column, `last_observed_at`, closes every row

Rows with fewer list items leave the extra columns empty. Text cells that begin with `=`, `+`,
`-` or `@` are prefixed with `'` so a spreadsheet does not run source data as a formula.
