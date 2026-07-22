# Production operations

## Deployment

Run from the production host as the deployment user:

```bash
/opt/hospital/scripts/deploy_production.sh
```

The script refuses to run unless the repository is on `main` with a clean
working tree. It then performs a fast-forward-only pull, builds an isolated
test container, runs the patient tests, deploys with Docker Compose, waits for
container health, and performs API smoke tests without patient data.

## Monitoring

`scripts/monitor_production.sh` checks:

- missing, stopped, unhealthy, or restarted containers;
- HTTP availability and recent HTTP 5xx responses;
- recent PostgreSQL errors;
- TLS certificate expiry within 21 days;
- root disk usage at or above 85%;
- Docker JSON log files larger than 500 MB.

Alerts are written to the system log with the `hospital-monitor` tag and to
`.tmp/monitor/alerts.log`. The `.tmp/` directory is ignored by Git.

For optional external alerts, put a Slack-compatible or Discord-compatible
webhook URL on one line in:

```text
~/.config/hospital-monitor/webhook-url
```

Set the file mode to `600`. Never commit the webhook URL.

The production user crontab runs the monitor every five minutes. Inspect local
alerts with:

```bash
tail -n 100 /opt/hospital/.tmp/monitor/alerts.log
journalctl -t hospital-monitor --since today
```
