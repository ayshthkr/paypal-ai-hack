# Render free-tier runbook

This project uses Render web services and a Render Postgres database. Render's current Free Postgres policy expires a database 30 days after creation; an expired database is inaccessible, and Render deletes it after a further 14-day grace period. Free databases do not have managed backups. Confirm current policy in [Render's free plan documentation](https://render.com/docs/free) and [Postgres recovery documentation](https://render.com/docs/postgresql-backups).

## Record the active database dates

The Render Dashboard was checked on November 11, 2026. It showed the database was created 18 hours earlier and listed an exact expiration date. The dashboard did not expose the exact creation timestamp in this view.

| Field | Value |
| --- | --- |
| Render database resource | `latepayer-db` from `render.yaml` |
| Created at | November 10, 2026 (dashboard showed “18 hours ago” on November 11; exact time not displayed) |
| Expires at | December 10, 2026 (Render Dashboard → database → Info) |
| Grace-period deletion date | December 24, 2026, based on Render's 14-day grace period; confirm current dashboard status before relying on this date |
| Calendar owner | Ayush Thakur |
| Reminder dates | 7 days before expiry and on expiry day |

Free database retention is separate from the Free web service's spin-down and monthly instance-hour limits. A keep-alive request cannot extend the database's expiration date.

## Seed the synthetic demo

`make seed-demo AS_OF=today` resets the synthetic fixture database, trains the model artifacts, and rescales demo predictions. Run it only when `DATABASE_URL` points to the environment you intend to reset. The default local workflow targets the local database; do not point a local shell at hosted production for this command. Keep provider actions in PayPal Sandbox and the configured recipient allowlist.

```sh
make seed-demo AS_OF=today
make smoke
```

The hosted API seeds synthetic records on first boot. Ordinary restarts preserve them. Use the dashboard's **Reset demo** control when an intentional synthetic-only reset is needed; this does not modify PayPal Sandbox records.

## Check availability and wake the demo

Render uses the API's minimal `/healthz` endpoint for the service health check. It returns only `{"ok": true}` and does not disclose database, authentication, PayPal, or model configuration. `/ready` is intentionally not exposed publicly.

```sh
curl -fsS https://latepayer-api.onrender.com/healthz
curl -fsS https://latepayer-web.onrender.com/
```

Free web services can spin down when inactive. Before a live demo, send the health request once and allow the first request to finish waking the service. If continuous availability is needed during a judging window, an external monitor can request `/healthz` every 10–14 minutes; this consumes the workspace's Free instance hours and does not prevent Postgres expiry. Do not rely on a monitor as a database backup or retention plan.

## If the service or database is down

1. Check the API and web service Events and Logs in the Render Dashboard.
2. Request `/healthz`; if it returns a non-2xx response, note the time and deployment ID.
3. If `/healthz` succeeds but the dashboard read fails, inspect the API logs and Postgres service state. The liveness endpoint deliberately does not run a database query.
4. Notify the project owner, Ayush Thakur, with the affected service, UTC time, deployment ID, and error summary. Do not include passwords, session cookies, provider payloads, or PayPal IDs in the report.
5. If the Free Postgres expiration date is near, upgrade the database or export data before expiry. Render does not provide managed backups for Free Postgres; see the recovery documentation linked above.
