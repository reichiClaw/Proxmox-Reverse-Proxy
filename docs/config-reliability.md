# Config publish reliability & Gate status UI

## Confirmed root causes

1. **Unreadable sibling YAML blocked Traefik’s file provider**  
   Gate admin runs with `UMask=0077` (`deploy/gate-admin.service`). Route files written without an explicit mode became `0600 gate:gate`. Traefik then logged `permission denied` on e.g. `apps/igotify.yml`, and the **entire** file-provider reload failed. Saved disk config (e.g. `pve.yml` → `https://10.0.0.4:8006`) stayed inactive while Traefik kept serving the previous upstream (`https://172.16.0.2:8006`).

2. **Nested `apps/` changes did not always recover the watcher**  
   After fixing permissions, only a top-level touch (`touch -c …/pve.yml`) triggered a successful reload. Nested-only edits after a failed callback were insufficient.

3. **“Saved” was treated as “active”**  
   The GUI had no Traefik runtime comparison, so a successful form POST looked like success while Traefik still used the old target.

## Reliable publish path (current)

1. Validate name, domain, upstream URL, path, YAML syntax.  
2. Backup the previous file under `config/backups/` (outside the watched tree).  
3. Atomically write the route YAML as **mode `0644`**, then `chmod` again after replace (UMask-safe).  
4. `ensure_dynamic_readable()` normalizes every `dynamic/**/*.yml` to `0644` before publish.  
5. Update top-level `dynamic/gate-revision.yml` so Traefik’s directory watcher always sees a top-level change.  
6. Poll local Traefik API `http://127.0.0.1:8090` until the active service URL matches (or timeout).  
7. Optionally probe backend direct + via Traefik (SNI).  
8. Persist Saved / Active / Checked timestamps in `config/runtime-status.json`.

Static config change: `config/traefik.yml` binds the API to **`127.0.0.1:8090`** (not public `:8080`, which is Gate GUI). **Restart Traefik once** after deploying that static change.

## Priority-1 UI

| Feature | Where |
|--------|--------|
| Saved / Active / Checked pills | Services list + edit page |
| Configured vs active upstream | Edit page + list mismatch hint |
| Save & verify | Create/update always verify; button on edit |
| Connection test | Direct + via Traefik |
| Clear takeover errors | Flash + provider/unreadable-file messages |
| Diagnose copy | `/diagnose` + `/diagnose.txt` (secrets redacted) |

## Deploy notes

```bash
cd /opt/gate
git pull
# Restart Traefik once for static API entryPoint (8090)
systemctl restart traefik
systemctl restart gate-admin
# Confirm API
curl -sS http://127.0.0.1:8090/api/http/services | head
```

Restore last good YAML from `config/backups/<name>.<stamp>.yml` into `config/dynamic/` (or `apps/`), ensure mode `0644`, then save again from the GUI (or touch `gate-revision.yml`).

## Acceptance mapping

| Criterion | Status |
|-----------|--------|
| GUI change active without manual touch/restart | Yes (revision nudge + 0644) |
| Nested / new / replaced files | Yes |
| Permissions survive re-edits | Explicit 0644 on write |
| Saved vs active visible | Yes |
| Validation/takeover errors shown | Yes |
| Previous config kept on failed publish | Backup before overwrite; Traefik keeps old if reload fails |
| PVE upstream `https://10.0.0.4:8006` | Repo template updated; live CT must match |
| Gate login / Gotify / other routes preserved | No router removals in this change |
| TLS vs reachability separated | Direct probe reports TLS separately; via-probe notes verify mode |
| Untested on live CT from this agent | Live Traefik/PVE/console not exercised in this environment |

## Ausbauplan (P2 / P3)

### P2 — next

1. **Change history & restore** — version `runtime-status` + backups in the UI; restore must rewrite app state + YAML and re-verify.  
2. **Certificate overview** — Client→Traefik (ACME) and Traefik→backend (`serversTransport` / rootCAs / serverName) with expiry.  
3. **Per-service logs** — parse Traefik access/provider logs filtered by router name; explain 502/504.

### P3 — later

4. Service templates (Proxmox, Gotify, generic HTTP/S) without default `insecureSkipVerify`.  
5. Searchable overview + free-form groups.  
6. Scheduled health checks (reachability ≠ config match).  
7. Gotify alerts for failed takeover / flapping / cert expiry (with dedupe; note proxy cannot self-report total outage).  
8. Conflict warnings (duplicate Host, overlapping PathPrefix, missing refs).  
9. Browser-friendly 502/504 pages without breaking APIs/WebSockets (review `Content-Type` / `error-pages`).  
10. Access profiles only if backed by real middlewares (allowlist / VPN), keeping Gate TOTP.

## Remaining uncertainties

- Why Traefik as `root` still hit `permission denied` on `0600 gate:gate` (possible sandbox/ACL); mitigated by `0644`, not fully root-caused.  
- Full PVE login/console and production ACME trust were not re-validated in this cloud agent environment.  
- Gotify long-lived streams: timeouts left at existing Traefik values; no change intended.
