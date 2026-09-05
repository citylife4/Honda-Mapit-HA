# Mapit Motorcycle Tracker - Home Assistant Integration

Track your motorcycle in real time using Home Assistant and the Mapit.me
vehicle tracking service.

## Features

- **Live GPS tracking** - position pushed over a websocket as the vehicle moves,
  backed by a REST poll every 10 minutes
- **Speed monitoring** - current speed in km/h, normalised to 0 while parked
- **Status detection** - know when your motorcycle is moving or at rest
- **Battery level** - track the tracker's battery
- **GPS accuracy** - accuracy/HDOP sensor with fallbacks across API payload shapes
- **Odometer** - total distance travelled
- **Route history** - route count, riding days, and the latest route's start,
  distance and duration
- **Multiple vehicles** - every vehicle on the account becomes its own device
- **Services** - fetch raw route data or export a route as GPX
- **Reauthentication** - prompts for credentials instead of silently breaking

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Install "Mapit Motorcycle Tracker" from HACS
3. Restart Home Assistant
4. Go to Settings → Devices & Services → Add Integration
5. Search for "Mapit Motorcycle Tracker"

### Manual Installation

1. Copy the `custom_components/mapit_tracker` folder into your Home Assistant
   `config/custom_components/` directory
2. Restart Home Assistant
3. Go to Settings → Devices & Services → Add Integration
4. Search for "Mapit Motorcycle Tracker"

Requires Home Assistant 2024.8 or newer.

## Configuration

All you need is your Mapit.me **email address** and **password**.

The AWS Cognito pool identifiers and API endpoints are discovered automatically
from the public Mapit web app at setup time, with built-in values as a fallback,
so there is nothing to look up in developer tools.

> Upgrading from 1.x? Your existing entry migrates automatically. The Cognito
> IDs you entered by hand are kept as a fallback and only used if discovery
> fails.

## Entities

For each vehicle on the account:

### Device tracker

- `device_tracker.<vehicle>` - GPS location, with speed, status, odometer and
  accuracy as attributes

### Sensors

| Sensor | Description | Enabled by default |
| --- | --- | --- |
| Speed | Current speed in km/h (0 while parked) | Yes |
| Status | Reported status (`MOVING`, `AT_REST`, …) | Yes |
| Battery | Battery level percentage | Yes |
| GPS accuracy | Accuracy reported by the tracker | Yes |
| Odometer | Total distance travelled | Yes |
| Last coordinate update | When the position was last reported | Yes |
| Last seen | When the device last reported anything | Yes |
| HDOP | Duplicate of GPS accuracy, kept for 1.x compatibility | No |
| Route count | Number of cached routes | No |
| Route days | Distinct days with a recorded route | No |
| Last route started / distance / duration | Latest route summary | No |

Disabled entities can be turned on from the device page.

### Binary sensor

- `binary_sensor.<vehicle>_moving` - on whenever the status is not `AT_REST`

## Services

### `mapit_tracker.get_route_detail`

Returns the raw route payload for a route ID.

```yaml
action: mapit_tracker.get_route_detail
data:
  route_id: rt-1234567890
response_variable: route
```

### `mapit_tracker.export_route_gpx`

Returns a GPX document built from a route's GeoJSON.

```yaml
action: mapit_tracker.export_route_gpx
data:
  route_id: rt-1234567890
response_variable: gpx_route
```

Both services accept an optional `config_entry_id` when more than one Mapit
account is configured.

## Usage Examples

### Automation: Notify when the motorcycle starts moving

```yaml
automation:
  - alias: "Motorcycle Started"
    trigger:
      - platform: state
        entity_id: binary_sensor.motorcycle_moving
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "Motorcycle Alert"
          message: "Your motorcycle has started moving!"
```

### Automation: Alert when the motorcycle exceeds a speed limit

```yaml
automation:
  - alias: "Motorcycle Speeding"
    trigger:
      - platform: numeric_state
        entity_id: sensor.motorcycle_speed
        above: 120
    action:
      - service: notify.mobile_app
        data:
          title: "Speed Alert"
          message: "Motorcycle is traveling at {{ states('sensor.motorcycle_speed') }} km/h"
```

### Card: Show location and status

```yaml
type: vertical-stack
cards:
  - type: map
    entities:
      - entity: device_tracker.motorcycle
    hours_to_show: 2

  - type: entities
    title: Motorcycle Status
    entities:
      - entity: binary_sensor.motorcycle_moving
        name: Moving
      - entity: sensor.motorcycle_speed
        name: Speed
      - entity: sensor.motorcycle_battery
        name: Battery Level
      - entity: sensor.motorcycle_gps_accuracy
        name: GPS Accuracy
      - entity: sensor.motorcycle_odometer
        name: Total Distance
      - entity: sensor.motorcycle_last_coordinate_update
        name: Last GPS Update
```

## Update Behaviour

Live position, speed, status and battery arrive over a websocket connection per
device, so the map updates as the vehicle moves rather than on a fixed interval.
A REST poll every 10 minutes refreshes vehicle details and fills any gap while
the socket is reconnecting; route summaries are cached for 6 hours.

## Troubleshooting

### "Cannot connect" error

- Check your internet connection
- Confirm the Mapit.me service is operational

### "Invalid auth" error

- Verify your email and password by signing in at <https://app.mapit.me>
- If the password changed, Home Assistant will prompt you to reauthenticate

### Entities not updating

- Check the Home Assistant logs for errors
- Enable debug logging to see the websocket traffic:

  ```yaml
  logger:
    logs:
      custom_components.mapit_tracker: debug
  ```

- Try reloading the integration from Settings → Devices & Services

### Stored session

The integration stores a refresh token under Home Assistant's `.storage`
directory so it does not have to replay a full password login on every restart.
Reloading the integration after a reauthentication clears it.

> Version 1.x wrote a `.mapit_tokens.json` file in the config directory. It is
> no longer read or written, and can be deleted after upgrading.

## Support

For issues and feature requests, please visit:
https://github.com/citylife4/Honda-Mapit-HA/issues

## License

MIT. Parts of the API client are derived from
[d3vv3/hass-honda-mapit](https://github.com/d3vv3/hass-honda-mapit); see the
repository's [LICENSE](../../LICENSE) for the retained notice.
