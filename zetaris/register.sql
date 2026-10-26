-- Paste into the Zetaris SQL editor (http://localhost:3000), ONE statement at a
-- time. Replace YOUR_POSTGRES_PASSWORD with POSTGRES_PASSWORD from the bundle's .env.
-- Four separate source systems = four datasources. Nothing is copied into Zetaris.

CREATE DATASOURCE accounts_src DESCRIBE BY "CloudNest account system" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://postgres:5432/resolve_accounts",
  username "zetaris",
  password "YOUR_POSTGRES_PASSWORD",
  fetchsize "1000"
)

REGISTER DATASOURCE TABLES FROM accounts_src

CREATE DATASOURCE kb_src DESCRIBE BY "CloudNest support knowledge base" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://postgres:5432/resolve_kb",
  username "zetaris",
  password "YOUR_POSTGRES_PASSWORD",
  fetchsize "1000"
)

REGISTER DATASOURCE TABLES FROM kb_src

CREATE DATASOURCE releases_src DESCRIBE BY "CloudNest release history" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://postgres:5432/resolve_releases",
  username "zetaris",
  password "YOUR_POSTGRES_PASSWORD",
  fetchsize "1000"
)

REGISTER DATASOURCE TABLES FROM releases_src

CREATE DATASOURCE issues_src DESCRIBE BY "CloudNest GitHub issue tracker" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://postgres:5432/resolve_issues",
  username "zetaris",
  password "YOUR_POSTGRES_PASSWORD",
  fetchsize "1000"
)

REGISTER DATASOURCE TABLES FROM issues_src

-- Smoke test: one statement across all four sources. Expect 4+ rows for c-031.
SELECT 'accounts' AS source, a.row_id FROM accounts_src.accounts a WHERE a.row_id = 'c-031'
UNION ALL SELECT 'kb', k.row_id FROM kb_src.kb k WHERE k.component = 'sync'
UNION ALL SELECT 'releases', r.row_id FROM releases_src.releases r WHERE r.component = 'sync'
UNION ALL SELECT 'issues', i.row_id FROM issues_src.issues i WHERE i.component = 'sync'
