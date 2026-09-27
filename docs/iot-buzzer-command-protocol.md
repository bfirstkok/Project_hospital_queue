# Wearable buzzer identify-command protocol

The web app can queue a short-lived `BUZZER` command for the active wearable
paired with a patient in observation monitoring. The repository currently
contains the Django server and a telemetry simulator, but no wearable firmware.
The physical buzzer will sound only after the device firmware implements this
poll-and-ack protocol.

## Preconditions

- The wearable is active and paired to the intended visit.
- The visit is in `OBSERVATION_MONITORING` or `REASSESSMENT_REQUIRED`.
- The wearable has sent telemetry within the last 60 seconds.
- Use HTTPS and keep the device API key private.

## Device polling

Poll `GET /api/iot/commands/next/` while paired, using the same headers as the
telemetry endpoint:

```http
X-DEVICE-ID: WATCH001
X-API-KEY: <device-api-key>
```

When there is no command, the server returns `{"ok": true, "command": null}`.
For a patient-identification request, it returns a command similar to:

```json
{
  "ok": true,
  "command": {
    "id": 123,
    "type": "BUZZER",
    "payload": {"pattern": "identify", "duration_ms": 1200},
    "expires_at": "2026-09-27T10:00:30+07:00"
  }
}
```

The firmware should activate its buzzer once for the requested pattern, then
acknowledge the result. The server marks a command delivered when it is returned
to the device, so polling it again will not trigger the same buzzer command a
second time. Unacknowledged commands expire after 30 seconds.

## Acknowledge execution

After the buzzer action completes, send:

```http
POST /api/iot/commands/123/ack/
Content-Type: application/json
X-DEVICE-ID: WATCH001
X-API-KEY: <device-api-key>
```

```json
{"result": "completed", "message": "buzzer played"}
```

If the device cannot sound the buzzer, acknowledge with `{"result":"failed",
"message":"<short reason>"}`. The web page waits for this acknowledgement and
reports success only when the device says it completed the action.

## Safety and behavior

- Requests are bound to the device's current active visit assignment.
- The server rejects inactive credentials, unpaired devices, and visits outside
  the monitoring workflow.
- The identify command is only for locating the person wearing the paired device;
  it does not change triage, queue order, or clinical alerts.
- Implement the physical pin/tone logic according to the specific watch board;
  this project does not assume a buzzer pin or a particular microcontroller.
