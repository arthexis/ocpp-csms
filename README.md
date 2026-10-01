# OCPP CSMS

A deliberately small Python OCPP 1.6J CSMS.

The first version keeps protocol handling direct and permissive. It is intended to become an appliance-style service: accept chargers, avoid blocking charging, preserve evidence, and expose only a small diagnostic surface.

## Run

```bash
python -m pip install -e ".[dev]"
ocpp-csms
```

By default the server listens on `0.0.0.0:9000` and accepts OCPP 1.6J charge points at:

```text
ws://localhost:9000/{charge_point_id}
```

## Layout

```text
src/ocpp_csms/
  app.py      # process startup
  server.py   # WebSocket accept loop
  session.py  # direct OCPP 1.6J handlers
  time.py     # timestamp helper
```

Persistence and appliance diagnostics will be added as explicit, small components rather than through another dispatch or service framework.
