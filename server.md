# Server Runbook

Updated: 2026-04-06

This document is a deployment runbook only.

Do not store production passwords, private keys, live IPs, access tokens, or direct login commands in this repository.
Retrieve operational secrets from the approved team vault or secret manager before connecting to production.

## Deployment Checklist

1. Open the approved credentials source and retrieve the current SSH target, user, and key material.
2. Connect to the production host with least-privilege credentials.
3. Change into the deploy directory for AutoVideoSrt.
4. Pull the intended revision or deploy the prepared artifact.
5. Restart the managed service.
6. Verify health checks, logs, and the externally configured reverse proxy.

## Required Production Controls

- Run the application behind a reverse proxy with TLS enabled.
- Avoid exposing the app process directly to the public internet.
- Run the service as a dedicated non-root user.
- Rotate credentials immediately if they were ever committed to source control.
- Keep host-specific details in the secure operations system, not in Git.
