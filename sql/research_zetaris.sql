-- Federated version of sql/research.sql. Four datasources, one statement.
-- Each datasource is a separate database registered in zetaris/register.sql.
-- If a table name is rejected, copy the exact path from the Zetaris UI catalog
-- (some setups need datasource.public.table).
WITH customer AS (
    SELECT row_id, client_version, last_working_version, payload
    FROM accounts_src.accounts WHERE row_id = ?
)
SELECT 'accounts' AS source, row_id, payload FROM customer
UNION ALL
SELECT 'kb', k.row_id, k.payload
FROM kb_src.kb k CROSS JOIN customer c WHERE k.component = ?
UNION ALL
SELECT 'releases', r.row_id, r.payload
FROM releases_src.releases r CROSS JOIN customer c WHERE r.component = ?
UNION ALL
SELECT 'issues', i.row_id, i.payload
FROM issues_src.issues i CROSS JOIN customer c
WHERE i.component = ? AND i.state = 'open'
  AND i.affects_version = c.client_version
