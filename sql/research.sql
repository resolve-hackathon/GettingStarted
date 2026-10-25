-- Four logical sources, one customer-scoped result set.
-- SQLite's ? placeholders need adapting to your Zetaris driver's paramstyle.
-- In Zetaris, map these names to virtual views over the separate live sources.
WITH customer AS (
    SELECT row_id, client_version, last_working_version, payload
    FROM accounts WHERE row_id = ?
)
SELECT 'accounts' AS source, row_id, payload FROM customer
UNION ALL
SELECT 'kb', k.row_id, k.payload
FROM kb k CROSS JOIN customer c WHERE k.component = ?
UNION ALL
SELECT 'releases', r.row_id, r.payload
FROM releases r CROSS JOIN customer c WHERE r.component = ?
UNION ALL
SELECT 'issues', i.row_id, i.payload
FROM issues i CROSS JOIN customer c
WHERE i.component = ? AND i.state = 'open'
  AND i.affects_version = c.client_version;
