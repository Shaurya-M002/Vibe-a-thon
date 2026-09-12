# Relay deployment

The portal runs as a systemd HTTP application bound to localhost, reached through the existing Cloudflare tunnel. SSH/SCP handles deployment transfers. No FTP service or additional public application port is required.

| Component | Location |
| --- | --- |
| Current release | `/srv/navsense/current` (release symlink) |
| Initial release | `/srv/navsense/releases/20260913-portal` |
| Private database | `/var/lib/navsense/recordings.sqlite` (owner-only permissions) |
| App service | `/etc/systemd/system/navsense-portal.service` |
| Tunnel configuration | `/etc/cloudflared/config.yml` |

The service runs:

```sh
/usr/bin/python3 /srv/navsense/current/portal_server.py \
  --database /var/lib/navsense/recordings.sqlite --port 8765
```

Both `navsense-portal` and `cloudflared` are enabled for reboot. Inspect health with:

```sh
sudo systemctl status navsense-portal cloudflared --no-pager
curl --fail http://127.0.0.1:8765/api/recordings
sudo journalctl -u navsense-portal -n 50 --no-pager
```

The Navsense ingress hostname is `navsense.sasanktumpati.com`, with service `http://127.0.0.1:8765`. Its proxied DNS CNAME must target the existing tunnel's `<tunnel-id>.cfargotunnel.com` address. An A record pointing at the relay bypasses the tunnel and will not reach the localhost application.

For updates, copy source into a new release directory, run `npm ci --prefix apartment`, change the `current` symlink, and restart only `navsense-portal`. Keep the database outside releases and Git. Do not replace the entire Cloudflare config: preserve other ingress rules and the final catch-all; validate any changed config before restarting `cloudflared`.

This deployment adds no application login. Public access is controlled by the Cloudflare configuration; playback endpoints contain recording summaries. The server blocks raw database and directory downloads.
