# Home Assistant Installation Guide

Complete guide for installing and configuring the Mapit Motorcycle Tracker integration in Home Assistant.

## Prerequisites

- Home Assistant 2023.1 or newer
- A Mapit.me account with an active vehicle
- Your Mapit.me login credentials
- AWS Cognito configuration details (see below)

## Installation Methods

### Method 1: HACS Installation (Recommended)

HACS (Home Assistant Community Store) makes installation and updates easier.

1. **Install HACS** (if not already installed):
   - Follow the [HACS installation guide](https://hacs.xyz/docs/setup/download)

2. **Add Custom Repository**:
   - Open HACS in Home Assistant
   - Click on **Integrations**
   - Click the **⋮** (three dots) in the top right corner
   - Select **Custom repositories**
   - Add repository URL: `https://github.com/citylife4/Honda-Mapit-HA`
   - Category: **Integration**
   - Click **Add**

3. **Install the Integration**:
   - In HACS → Integrations, search for "Mapit Motorcycle Tracker"
   - Click on it
   - Click **Download**
   - Choose the latest version
   - Click **Download**

4. **Restart Home Assistant**:
   - Go to **Settings** → **System** → **Restart**

5. **Add the Integration**:
   - Go to **Settings** → **Devices & Services**
   - Click **+ Add Integration**
   - Search for "Mapit Motorcycle Tracker"
   - Follow the configuration wizard

### Method 2: Manual Installation

### Step 1: Copy Integration Files

1. Access your Home Assistant configuration directory (where `configuration.yaml` is located)
2. Create a `custom_components` folder if it doesn't exist
3. Copy the entire `custom_components/mapit_tracker` folder into your `custom_components` directory

Your directory structure should look like:
```
config/
├── configuration.yaml
└── custom_components/
    └── mapit_tracker/
        ├── __init__.py
        ├── config_flow.py
        ├── device_tracker.py
        ├── sensor.py
        ├── api.py
        ├── manifest.json
        ├── strings.json
        ├── translations/
        │   └── en.json
        └── README.md
```

### Step 2: Restart Home Assistant

Restart Home Assistant to load the new integration:
- Go to **Settings** → **System** → **Restart**
- Or use the command: `ha core restart` (for supervised installations)

### Step 3: Add the Integration

1. Go to **Settings** → **Devices & Services**
2. Click the **+ Add Integration** button
3. Search for "Mapit Motorcycle Tracker"
4. Click on it to start the configuration

## Configuration

### What You Need

1. **Email Address** - Your Mapit.me login email
2. **Password** - Your Mapit.me password

Nothing else. Since version 2.0 the integration discovers the AWS Cognito pool
identifiers and API endpoints from the public Mapit web app when you set it up,
falling back to built-in values if that lookup fails.

Config entries created with version 1.x are migrated automatically on upgrade:
the pool IDs you entered by hand are retained as a fallback and only used if
discovery fails.


### Configuration Steps

1. When adding the integration, you'll see a form with 5 fields
2. Enter your email address and password
3. Enter the three AWS Cognito configuration IDs
4. Click **Submit**
5. The integration will validate your credentials
6. On success, your motorcycle will appear as a new device

## Entities Created

After successful setup, you'll have these entities:

### Device Tracker
- `device_tracker.motorcycle` - Location on map

### Sensors
- `sensor.motorcycle_speed` - Speed in km/h
- `sensor.motorcycle_status` - MOVING or AT_REST
- `sensor.motorcycle_battery` - Battery percentage
- `sensor.motorcycle_gps_accuracy` - GPS accuracy value from Mapit API
- `sensor.motorcycle_hdop` - HDOP compatibility sensor
- `sensor.motorcycle_odometer` - Odometer in km (if available)
- `sensor.motorcycle_last_coordinate_update` - Last GPS coordinate timestamp

## Adding to Dashboard

### Quick Map Card

```yaml
type: map
entities:
  - device_tracker.motorcycle
hours_to_show: 2
```

### Detailed Status Card

```yaml
type: entities
title: Motorcycle
entities:
  - entity: sensor.motorcycle_status
    icon: mdi:motorbike
  - entity: sensor.motorcycle_speed
    icon: mdi:speedometer
  - entity: sensor.motorcycle_battery
    icon: mdi:battery
```

### Combined Card

```yaml
type: vertical-stack
cards:
  - type: map
    entities:
      - device_tracker.motorcycle
    hours_to_show: 2
    
  - type: glance
    title: Quick Status
    entities:
      - entity: sensor.motorcycle_status
      - entity: sensor.motorcycle_speed
      - entity: sensor.motorcycle_battery
```

## Example Automations

### Notification When Motorcycle Moves

```yaml
automation:
  - alias: "Motorcycle Movement Alert"
    description: "Notify when motorcycle starts moving"
    trigger:
      - platform: state
        entity_id: sensor.motorcycle_status
        to: "MOVING"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "🏍️ Motorcycle Alert"
          message: "Your motorcycle has started moving!"
          data:
            notification_icon: mdi:motorbike
            actions:
              - action: "VIEW_MAP"
                title: "View Location"
```

### Speed Limit Alert

```yaml
automation:
  - alias: "Motorcycle Speed Alert"
    description: "Alert when speed exceeds 100 km/h"
    trigger:
      - platform: numeric_state
        entity_id: sensor.motorcycle_speed
        above: 100
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "⚠️ Speed Alert"
          message: "Speed: {{ states('sensor.motorcycle_speed') }} km/h"
```

### Motorcycle at Rest Notification

```yaml
automation:
  - alias: "Motorcycle Stopped"
    description: "Notify when motorcycle stops for 5 minutes"
    trigger:
      - platform: state
        entity_id: sensor.motorcycle_status
        to: "AT_REST"
        for:
          minutes: 5
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "🏍️ Motorcycle Stopped"
          message: "Motorcycle has been stationary for 5 minutes at {{ state_attr('device_tracker.motorcycle', 'latitude') }}, {{ state_attr('device_tracker.motorcycle', 'longitude') }}"
```

## Troubleshooting

### Integration Not Appearing

- Ensure you copied all files to `custom_components/mapit_tracker/`
- Restart Home Assistant after copying files
- Check the Home Assistant logs for errors: Settings → System → Logs

### "Cannot Connect" Error

- Verify your internet connection
- Check that Mapit.me service is accessible
- Ensure your email and password are correct

### "Invalid Auth" Error

- Double-check all three AWS Cognito IDs
- Ensure there are no extra spaces in the IDs
- Verify the IDs are from your Mapit.me account

### Entities Not Updating

- Check Settings → Devices & Services → Mapit Motorcycle Tracker
- Look for error messages
- Live updates arrive over a websocket; the REST poll runs every 10 minutes
- Check Home Assistant logs for API errors

### Token Cache Issues

If authentication fails repeatedly:
1. Go to your Home Assistant config directory
2. Reload the integration from Settings -> Devices & Services
3. Restart Home Assistant
4. The integration will re-authenticate

Note: the integration automatically retries once by clearing expired cached tokens before this manual step is needed.

### View Logs

Enable debug logging for detailed troubleshooting:

```yaml
logger:
  default: info
  logs:
    custom_components.mapit_tracker: debug
```

Add this to `configuration.yaml`, restart, and check the logs.

## Uninstalling

1. Go to Settings → Devices & Services
2. Find "Mapit Motorcycle Tracker"
3. Click the three dots menu → Delete
4. Remove the `custom_components/mapit_tracker` folder
5. Restart Home Assistant

## Support & Issues

For problems or questions:
- Check the logs first
- Review this guide
- Open an issue at: https://github.com/citylife4/Honda-Mapit-HA/issues

## Privacy & Security

- Your credentials are stored securely in Home Assistant's config entry
- The refresh token is stored in Home Assistant's `.storage` directory
- The integration only communicates with Mapit.me servers
- No data is sent to third parties

## Updates

To update the integration:
1. Download the latest version
2. Replace the files in `custom_components/mapit_tracker/`
3. Restart Home Assistant
4. Check the release notes for breaking changes
