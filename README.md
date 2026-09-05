# Mapit Vehicle Tracking System

A comprehensive motorcycle tracking solution that interfaces with the Mapit.me vehicle tracking API. Provides both a standalone Python application and a Home Assistant integration for real-time GPS tracking.

## Features

### Standalone Application
- Real-time GPS location tracking
- Speed and status monitoring (MOVING/AT_REST)
- Data storage in Oracle and MongoDB databases
- Web-based live map visualization with Leaflet.js
- Export location history to GeoJSON and KML formats
- Multiple operation modes (continuous polling, change detection)
- Systemd service for background operation

### Home Assistant Integration
- Fully async integration built on Home Assistant's shared aiohttp session
- Live position pushed over a websocket, backed by a 10 minute REST poll
- Email/password setup - the AWS Cognito settings are discovered automatically
- Multiple vehicles per account, each as its own Home Assistant device
- Speed, status, battery, GPS accuracy, odometer and route sensors
- A `moving` binary sensor, plus route detail and GPX export services
- Reauthentication flow and token reuse across restarts

## Quick Start

### For Home Assistant Users

#### HACS Installation (Recommended)

1. Add this repository as a custom repository in HACS:
   - Go to HACS → Integrations → ⋮ (menu) → Custom repositories
   - Add URL: `https://github.com/citylife4/Honda-Mapit-HA`
   - Category: Integration
2. Search for "Mapit Motorcycle Tracker" in HACS
3. Click Install
4. Restart Home Assistant
5. Go to Settings → Devices & Services → Add Integration
6. Search for "Mapit Motorcycle Tracker" and follow the setup wizard

#### Manual Installation

1. Copy `custom_components/mapit_tracker` to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to Settings → Devices & Services → Add Integration
4. Search for "Mapit Motorcycle Tracker" and follow the setup wizard

See [Home Assistant Integration README](custom_components/mapit_tracker/README.md) for detailed instructions.

### For Standalone Application Users

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Create `settings.py` with your credentials:
   ```python
   mappit_username = "your_email@example.com"
   mappit_password = "your_password"
   mappit_identityPoolId = "eu-west-1:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
   mappit_userPoolId = "eu-west-1_XXXXXXXXX"
   mappit_userPoolWebClientId = "xxxxxxxxxxxxxxxxxxxxxxxxxx"
   oracle_user = "ADMIN"
   oracle_password = "your_oracle_password"
   oracle_dns = "your_adb_tns_name"
   mongo_url = "mongodb://localhost:27017/"
   ```

3. Run the application:
   ```bash
   # Single query
   python mapit.py
   
   # Continuous tracking
   python mapit.py --continuous
   
   # Web map server
   python mapit.py --serve-map --map-port 8080
   ```

## Documentation

- [AGENTS.md](AGENTS.md) - Detailed architecture and API documentation
- [Home Assistant Integration](custom_components/mapit_tracker/README.md) - HA setup guide
- [HACS Deployment](docs/HACS_DEPLOYMENT.md) - Guide for HACS submission and releases
- [mapit-tracker.service](mapit-tracker.service) - Systemd service configuration

## Requirements

- Python 3.9+
- Mapit.me account and API credentials
- Optional: Oracle Autonomous Database for data storage
- Optional: MongoDB for additional storage
- Optional: Home Assistant 2024.8+ for the integration

## Testing

The integration's payload parsing is covered by unit tests that run without a
Home Assistant install:

```bash
pip install -r requirements-test.txt
pytest tests/
```

## License

Released under the MIT License - see [LICENSE](LICENSE).

The Home Assistant integration's Cognito/SigV4 authentication, runtime config
discovery, websocket handling and GPX export are derived from
[d3vv3/hass-honda-mapit](https://github.com/d3vv3/hass-honda-mapit), used under
the MIT License. The full upstream notice is retained in [NOTICE](NOTICE).
