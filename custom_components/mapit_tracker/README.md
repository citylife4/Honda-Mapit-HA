# Mapit Motorcycle Tracker - Home Assistant Integration

Track your motorcycle's location in real-time using Home Assistant and the Mapit.me vehicle tracking service.

## Features

- **Real-time GPS tracking** - See your motorcycle's current location on the map
- **Speed monitoring** - Track current speed in km/h (normalized to 0 when at rest)
- **Status detection** - Know when your motorcycle is moving or at rest
- **Battery level** - Track motorcycle battery status
- **HDOP monitoring** - GPS precision indicator (Horizontal Dilution of Precision)
- **Odometer tracking** - Monitor total distance traveled
- **Last coordinate timestamp** - See when GPS data was last updated
- **Device tracker** - Full integration with Home Assistant's device tracker
- **Comprehensive sensors** - Individual sensors for all vehicle telemetry

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Install "Mapit Motorcycle Tracker" from HACS
3. Restart Home Assistant
4. Go to Settings → Devices & Services → Add Integration
5. Search for "Mapit Motorcycle Tracker"

### Manual Installation

1. Copy the `custom_components/mapit_tracker` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to Settings → Devices & Services → Add Integration
4. Search for "Mapit Motorcycle Tracker"

## Configuration

You will need the following information from your Mapit.me account:

- **Email address** - Your Mapit.me login email
- **Password** - Your Mapit.me password
- **AWS Identity Pool ID** - Format: `eu-west-1:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
- **AWS User Pool ID** - Format: `eu-west-1_XXXXXXXXX`
- **AWS User Pool Client ID** - Format: `xxxxxxxxxxxxxxxxxxxxxxxxxx`

### Finding AWS Configuration

The AWS configuration values can be found by:
1. Logging into your Mapit.me account
2. Inspecting the network traffic in your browser's developer tools
3. Looking for the authentication requests to AWS Cognito

For convenience, these are typically consistent for Mapit.me users:
- Region: `eu-west-1`
- Service endpoints use the format shown above

## Entities

Once configured, the integration will create the following entities:

### Device Tracker
- `device_tracker.motorcycle` - GPS location on the map with speed, status, and additional telemetry attributes

### Sensors
- `sensor.motorcycle_speed` - Current speed in km/h (0 when at rest)
- `sensor.motorcycle_status` - Current status (MOVING or AT_REST)
- `sensor.motorcycle_battery` - Battery level percentage
- `sensor.motorcycle_hdop` - GPS Horizontal Dilution of Precision (lower is better)
- `sensor.motorcycle_odometer` - Total distance traveled in km (if available)
- `sensor.motorcycle_last_coordinate_update` - Timestamp of last GPS coordinate update

## Usage Examples

### Automation: Notify when motorcycle starts moving

```yaml
automation:
  - alias: "Motorcycle Started"
    trigger:
      - platform: state
        entity_id: sensor.motorcycle_status
        to: "MOVING"
    action:
      - service: notify.mobile_app
        data:
          title: "Motorcycle Alert"
          message: "Your motorcycle has started moving!"
```

### Automation: Alert when motorcycle exceeds speed limit

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

### Card: Show motorcycle location and status

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
      - entity: sensor.motorcycle_status
        name: Status
      - entity: sensor.motorcycle_speed
        name: Speed
      - entity: sensor.motorcycle_battery
        name: Battery Level
      - entity: sensor.motorcycle_hdop
        name: GPS Precision (HDOP)
      - entity: sensor.motorcycle_odometer
        name: Total Distance
      - entity: sensor.motorcycle_last_coordinate_update
        name: Last GPS Update
```

## Polling Interval

The integration polls the Mapit.me API every 30 seconds by default. This provides a good balance between real-time updates and API usage.

## Troubleshooting

### "Cannot connect" error
- Verify your email and password are correct
- Check your internet connection
- Ensure the Mapit.me service is operational

### "Invalid auth" error
- Double-check your AWS configuration IDs
- Make sure you're using the correct Identity Pool ID, User Pool ID, and Client ID

### Entities not updating
- Check the Home Assistant logs for errors
- Verify the integration is enabled in Settings → Devices & Services
- Try removing and re-adding the integration

### Token cache
The integration caches authentication tokens in `.mapit_tokens.json` in your Home Assistant config directory. Expired tokens are now automatically cleared and refreshed once. If authentication still fails, delete this file and restart Home Assistant.

## Support

For issues and feature requests, please visit:
https://github.com/citylife4/hondamapitapi/issues

## License

This integration is provided as-is for use with the Mapit.me vehicle tracking service.
