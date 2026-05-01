# Machine ID Security

Stand: 2026-05-01

Der Player nutzt die bestehende Maschinen-ID als feste Geräteidentität für Link/ReLink/Sync.

Reihenfolge der Auflösung:

1. `PORTAL_MACHINE_ID` oder `JARVIS_MACHINE_ID`
2. Device-Portal JSON (`machine_id`):
   - `/home/djanebmb/projects/Joormann-Media-Deviceportal/var/data/device.json`
   - `/opt/joormann-media-deviceportal/var/data/device.json`
   - optional Override via `DEVICE_PORTAL_DEVICE_JSON`
3. Linux Fallback:
   - `/etc/machine-id`
   - `/var/lib/dbus/machine-id`

Ziel: stabile Zuordnung im Portal als Sicherheitsfeature.
