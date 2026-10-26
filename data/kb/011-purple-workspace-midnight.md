<!-- resolve: {"id":"KB-011","component":"appearance","min_version":"3.7.0","max_version":"3.8.2","terms":["purple","violet","midnight"]} -->
# Workspace turns purple at midnight

This is an authored support scenario for the fictional CloudNest demo product.
On clients 3.7.0 through 3.8.2, a workspace appearance rule can select the Purple
theme at 00:00 in the device's local time zone. It changes the workspace's
appearance; it does not indicate a sync or account-health problem. Check the
schedule before assuming this is the cause.

1. Open CloudNest Settings > Appearance > Theme schedule.
2. Look for an enabled rule that starts at 00:00 (midnight) and selects Purple or Violet; check the device's time zone too.
3. If that rule exists, disable or remove it. Turn off Scheduled themes if you do not want any automatic theme changes.
4. Select your preferred Light, Dark, or System theme, choose Apply, and reload the workspace.
5. If no matching rule exists or the workspace turns purple again, ask a support engineer to investigate and include your client version, local time, and time zone.
