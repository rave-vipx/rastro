-- Approved fields are untrusted source data. Missing/stale records are retained.
SELECT json_extract(payload, '$.fields.starts_at') AS starts_at,
       json_extract(payload, '$.fields.title') AS title,
       json_extract(payload, '$.fields.venue') AS venue,
       json_extract(payload, '$.last_observed_at') AS last_observed_at
FROM dataset_records
WHERE dataset = 'uy_dance_agenda'
  AND julianday(json_extract(payload, '$.fields.starts_at')) >= julianday('now')
ORDER BY julianday(json_extract(payload, '$.fields.starts_at')), title, key;
