# Machine ID + Portal URL Security

Stand: 2026-05-01

Der Player nutzt bestehende Geräteidentität und Portal-Basis zentral aus dem Device-Portal.

## Machine ID Auflösung

1. `PORTAL_MACHINE_ID` oder `JARVIS_MACHINE_ID`
2. Device-Portal JSON (`machine_id`):
   - `/home/djanebmb/projects/Joormann-Media-Deviceportal/var/data/device.json`
   - `/opt/joormann-media-deviceportal/var/data/device.json`
   - optional Override via `DEVICE_PORTAL_DEVICE_JSON`
3. Linux Fallback:
   - `/etc/machine-id`
   - `/var/lib/dbus/machine-id`

## Portal URL Auflösung

1. `PORTAL_URL` oder `JARVIS_PORTAL_URL`
2. Device-Portal Config:
   - `/home/djanebmb/projects/Joormann-Media-Deviceportal/var/data/config.json`
   - `/opt/joormann-media-deviceportal/var/data/config.json`
   - Felder: `admin_base_url`, `portal_url`, `base_url`
   - optional Override via `DEVICE_PORTAL_CONFIG_JSON`

Damit werden Link/ReLink/Sync/Heartbeat zentral und konsistent gefahren.
