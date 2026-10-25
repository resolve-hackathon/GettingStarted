# CloudNest changelog (fictional demo product)

## 3.8.2 - 2026-10-21
<!-- resolve: {"id":"3.8.2-upload","version":"3.8.2","component":"upload","terms":["upload","crash"],"regression":true,"regression_signals":["upload","crash"]} -->
The resumable upload worker changed in 3.8.2. Internal synthetic QA reproduced an
upload crash when resuming a paused transfer. No workaround is verified yet.

## 3.8.1 - 2026-10-20
<!-- resolve: {"id":"3.8.1-sync","version":"3.8.1","component":"sync","terms":["sync","stopped","stuck"],"regression":false} -->
The desktop sync worker was rewritten. A subset of Windows clients stop syncing
after upgrading; the synthetic issue fixture GH-182 tracks this regression.

## 3.8.0 - 2026-10-15
<!-- resolve: {"id":"3.8.0-search","version":"3.8.0","component":"search","terms":["search","index"],"regression":false} -->
Search now uses incremental indexing. No new regression was reported in QA.

## 3.7.4 - 2026-10-10
<!-- resolve: {"id":"3.7.4-exports","version":"3.7.4","component":"exports","terms":["blank","empty","export"],"regression":false} -->
The exports panel remembers the previous date filter. An empty date range may
produce a blank export. KB-002 documents resetting the filter for 3.7.x.

## 3.7.3 - 2026-10-05
<!-- resolve: {"id":"3.7.3-sharing","version":"3.7.3","component":"sharing","terms":["invite","sharing"],"regression":false} -->
Sharing invitations expire after seven days and can be resent from the members panel.

## 3.7.0 - 2026-09-28
<!-- resolve: {"id":"3.7.0-login","version":"3.7.0","component":"login","terms":["password","login"],"regression":false} -->
Added the password reset screen. Reset links expire after fifteen minutes.
