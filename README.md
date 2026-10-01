# OCPP CSMS

A small Python OCPP CSMS skeleton.

The first version keeps the protocol layer thin and pushes charger admission,
authorization, transactions, and future persistence into explicit services.

## Run

```bash
python -m pip install -e ".[dev]"
ocpp-csms
```

By default the server listens on `0.0.0.0:9000` and accepts OCPP 1.6J charge
points at:

```text
ws://localhost:9000/{charge_point_id}
```

## Layout

```text
src/ocpp_csms/
  app.py              # Startup composition
  server.py           # WebSocket accept loop
  session.py          # Thin OCPP charge point session
  routing.py          # Handler registry
  handlers/           # OCPP message handlers
  services/           # Business services
```
