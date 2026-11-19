# Field contracts

A job declares the shape of each record with `fields`. Besides the scalar types
(`string`, `integer`, `number`, `decimal`, `boolean`, `date`, `datetime`, `url`) a field can be a
bounded `list` or an `object`.

## Lists and objects

```yaml
fields:
  - name: tickets
    type: list
    max_items: 20
    item:
      name: item
      type: object
      members:
        - {name: tier, type: string}
        - {name: price, type: decimal}
```

- An `object` holds uniquely named scalar members. Objects do not nest in objects.
- A `list` holds scalars or objects. Lists do not nest in lists.
- `max_items` (default 100, at most 1000) bounds the input; `min_items` is checked after
  invalid items are dropped.
- The `item` name is ignored.

## What happens to invalid parts

Invalid data never becomes a silent success. Recoveries are reported as issues:

| Situation | Result | Issue code |
| --- | --- | --- |
| A list item fails validation | The item is dropped | `list_item_dropped` |
| An optional object member fails validation | The member becomes `null` | `member_nulled` |
| A required member fails, or the list falls below `min_items` | The record is rejected | none |

Issue messages never include the rejected value.

## Placeholders

`null_values` lists text that stands for "no value" on a site, for example `n/a` or
`0000-00-00`. Matching ignores surrounding whitespace and case. A matching value fails
validation with the type `null_placeholder`, so the record is rejected (required field) or
the field is reported (optional field) instead of storing the placeholder.

## Dates without an offset

A `datetime` with no UTC offset takes the region timezone of the run. The result carries the
offset (for example `-03:00` in `America/Argentina/Buenos_Aires`) and the run reports
`naive_datetime_localized` for that field. Without a region timezone the value stays naive.
