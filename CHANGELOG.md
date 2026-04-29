# Changelog

## 1.0.2

- Classify UniFi `Temporary Internet Disconnection` alerts as WAN outage events so per-WAN outage counters increment correctly.
- Avoid counting restored/reconnected wording as outage events.

## 1.0.0

- Added polished brand assets for HACS/GitHub usage


Initial stable release.

### Added

1. UniFi Network WAN health and WAN configuration sensors
2. Internet health state, routing mode and stability score sensors
3. Native SNMP v2c traffic polling with live rates and total counters
4. Utility Meter ready total sensors for WAN1, WAN2 and combined internet traffic
5. UniFi Alarm Manager webhook receiver with local WAN event history
6. Remote User VPN and Teleport detection
7. Integration API client using `X-API-KEY` without UniFi OS session cookies
8. Options flow for SNMP, Teleport, API key and webhook configuration
9. German and English translations
10. Example Utility Meter package, optional connectivity helper package and example NOC dashboard

### Changed

1. Removed bulky VPN debug attributes from the public entity attributes
2. Reduced coordinator payload by no longer storing raw client inventory snapshots
3. Set `sensor.unifi_network_monitor_internet_total_mbps` icon to `mdi:speedometer`
