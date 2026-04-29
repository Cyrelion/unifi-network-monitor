# UniFi Network Monitor

<p align="center">
  <img src="brands/unifi_network_monitor/logo.png" alt="UniFi Network Monitor" width="160">
</p>


Native Home Assistant custom integration for monitoring UniFi Gateway WAN health, failover events, SNMP traffic and VPN status.

Repository URL:

```text
https://github.com/Cyrelion/unifi-network-monitor
```

## Version

Current stable version: `1.0.0`

## Features

1. UniFi Network WAN configuration and WAN health via the local UniFi OS API
2. Internet health state, routing mode and stability score
3. SNMP v2c WAN traffic polling with live rates and total counters
4. Utility Meter ready traffic total sensors
5. UniFi Alarm Manager webhook receiver with local WAN event history
6. Remote User VPN status detection
7. Teleport detection via the UniFi Network Integration API plus local client inventory fallback
8. Optional helper package for independent DNS, AdGuard DNS and HTTP checks
9. Example NOC dashboard

## Repository layout

```text
custom_components/unifi_network_monitor/
examples/packages/unifi_network_monitor_helpers.yaml
examples/packages/unifi_network_monitor_connectivity_helpers.yaml
examples/dashboards/90_noc_unifi_network_monitor.yaml
brands/unifi_network_monitor/icon.png
brands/unifi_network_monitor/logo.png
assets/unifi-network-monitor-icon.svg
```

## Brand assets

The repository includes ready-to-use brand assets:

```text
brands/unifi_network_monitor/icon.png
brands/unifi_network_monitor/logo.png
assets/unifi-network-monitor-icon.svg
assets/unifi-network-monitor-icon.png
assets/unifi-network-monitor-logo.png
```

The icon is custom made for this integration and intentionally avoids using official Ubiquiti or UniFi trademarks.

## Requirements

1. Home Assistant `2025.11.0` or newer
2. UniFi OS gateway with local Network API access
3. Local UniFi user credentials
4. Optional UniFi Network Integration API key for richer Teleport detection
5. Optional SNMP v2c enabled on the UniFi gateway for traffic counters

## Installation with HACS custom repository

Add this repository as a custom integration repository in HACS:

```text
https://github.com/Cyrelion/unifi-network-monitor
```

Then install **UniFi Network Monitor**, restart Home Assistant, and add the integration from:

```text
Settings > Devices & services > Add integration > UniFi Network Monitor
```

## Manual installation

Copy this directory:

```text
custom_components/unifi_network_monitor
```

to:

```text
/config/custom_components/unifi_network_monitor
```

Restart Home Assistant and add the integration from the UI.

## Basic configuration

Typical UDM Pro values:

```text
Host: 192.168.10.1
Site: default
Site name: Default
Verify SSL certificate: false
Scan interval: 30
```

After setup, open:

```text
Settings > Devices & services > UniFi Network Monitor > Configure
```

Useful options:

```text
Teleport IP range: 192.168.2.0/24
Teleport client matchers: optional fallback list
Integration API key: optional, recommended for Teleport
SNMP enabled: true or false
SNMP host: usually the gateway IP
SNMP community: your SNMP community
WAN1 and WAN2 OID overrides: device specific
Webhook enabled: true or false
Webhook ID: custom ID or generated ID
```

## UniFi Integration API key

Teleport detection works best with the local UniFi Network Integration API.

Create the API key in UniFi Network:

```text
Settings > Control Plane > Integrations
```

Use that key in the integration options. The key is sent as `X-API-KEY` without UniFi OS session cookies, matching the request style used by the previously working shell package.

## VPN and Teleport detection

Remote User VPN is read from:

```text
/proxy/network/api/s/<site>/stat/remoteuservpn
```

If UniFi does not list individual remote VPN rows, the integration falls back to the VPN health summary and creates placeholder rows for active clients.

Teleport is detected by:

1. UniFi Network Integration API clients
2. Local UniFi client inventory rows whose IP address matches the configured Teleport range
3. Optional Teleport client matchers

Relevant entities:

```text
sensor.unifi_network_monitor_vpn_status_raw
sensor.unifi_network_monitor_vpn_active_count
sensor.unifi_network_monitor_vpn_remote_user_count
sensor.unifi_network_monitor_vpn_teleport_count
sensor.unifi_network_monitor_vpn_connected_users
binary_sensor.unifi_network_monitor_vpn_active
binary_sensor.unifi_network_monitor_teleport_active
binary_sensor.unifi_network_monitor_remote_user_vpn_active
```

The raw VPN status sensor intentionally exposes only normalized VPN rows, candidate lists and lightweight metadata. Bulky debug snapshots are not exposed as entity attributes in `1.0.0`.

## SNMP traffic support

The integration includes a small internal SNMP v2c GET client for WAN Counter64 OIDs. No external Python requirement is needed.

Default UDM Pro assumptions:

```text
WAN1 RX: 1.3.6.1.2.1.31.1.1.1.6.3
WAN1 TX: 1.3.6.1.2.1.31.1.1.1.10.3
WAN2 RX: 1.3.6.1.2.1.31.1.1.1.6.5
WAN2 TX: 1.3.6.1.2.1.31.1.1.1.10.5
```

Other UniFi gateways can use different interface indexes. Verify the OIDs for your device and override them in the integration options.

Useful SNMP entities:

```text
sensor.unifi_network_monitor_wan1_rx_rate
sensor.unifi_network_monitor_wan1_tx_rate
sensor.unifi_network_monitor_wan2_rx_rate
sensor.unifi_network_monitor_wan2_tx_rate
sensor.unifi_network_monitor_internet_total_mbps
sensor.unifi_network_monitor_wan1_rx_total
sensor.unifi_network_monitor_wan1_tx_total
sensor.unifi_network_monitor_wan2_rx_total
sensor.unifi_network_monitor_wan2_tx_total
sensor.unifi_network_monitor_internet_total
binary_sensor.unifi_network_monitor_snmp_traffic_healthy
```

The first rate sample after a restart is normally `0`, because two SNMP counter samples are needed to calculate a delta.

## Traffic totals and Utility Meter helper package

The integration provides source sensors with `state_class: total_increasing` for rollups.

Recommended source sensors:

```text
sensor.unifi_network_monitor_wan1_rx_total
sensor.unifi_network_monitor_wan1_tx_total
sensor.unifi_network_monitor_wan2_rx_total
sensor.unifi_network_monitor_wan2_tx_total
sensor.unifi_network_monitor_internet_rx_total
sensor.unifi_network_monitor_internet_tx_total
sensor.unifi_network_monitor_internet_total
```

Copy the included helper file to:

```text
/config/packages/unifi_network_monitor_helpers.yaml
```

Make sure packages are enabled in `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Full helper package:

```yaml
# UniFi Network Monitor helper package
# Place this file in /config/packages/unifi_network_monitor_helpers.yaml
# Requires packages to be enabled in configuration.yaml

utility_meter:
  unifi_network_monitor_wan1_rx_daily_gb:
    source: sensor.unifi_network_monitor_wan1_rx_total
    name: UniFi Network Monitor WAN1 RX Daily GB
    unique_id: unifi_network_monitor_wan1_rx_daily_gb
    cycle: daily
    always_available: true

  unifi_network_monitor_wan1_rx_weekly_gb:
    source: sensor.unifi_network_monitor_wan1_rx_total
    name: UniFi Network Monitor WAN1 RX Weekly GB
    unique_id: unifi_network_monitor_wan1_rx_weekly_gb
    cycle: weekly
    always_available: true

  unifi_network_monitor_wan1_rx_monthly_gb:
    source: sensor.unifi_network_monitor_wan1_rx_total
    name: UniFi Network Monitor WAN1 RX Monthly GB
    unique_id: unifi_network_monitor_wan1_rx_monthly_gb
    cycle: monthly
    always_available: true

  unifi_network_monitor_wan1_rx_yearly_gb:
    source: sensor.unifi_network_monitor_wan1_rx_total
    name: UniFi Network Monitor WAN1 RX Yearly GB
    unique_id: unifi_network_monitor_wan1_rx_yearly_gb
    cycle: yearly
    always_available: true

  unifi_network_monitor_wan1_tx_daily_gb:
    source: sensor.unifi_network_monitor_wan1_tx_total
    name: UniFi Network Monitor WAN1 TX Daily GB
    unique_id: unifi_network_monitor_wan1_tx_daily_gb
    cycle: daily
    always_available: true

  unifi_network_monitor_wan1_tx_weekly_gb:
    source: sensor.unifi_network_monitor_wan1_tx_total
    name: UniFi Network Monitor WAN1 TX Weekly GB
    unique_id: unifi_network_monitor_wan1_tx_weekly_gb
    cycle: weekly
    always_available: true

  unifi_network_monitor_wan1_tx_monthly_gb:
    source: sensor.unifi_network_monitor_wan1_tx_total
    name: UniFi Network Monitor WAN1 TX Monthly GB
    unique_id: unifi_network_monitor_wan1_tx_monthly_gb
    cycle: monthly
    always_available: true

  unifi_network_monitor_wan1_tx_yearly_gb:
    source: sensor.unifi_network_monitor_wan1_tx_total
    name: UniFi Network Monitor WAN1 TX Yearly GB
    unique_id: unifi_network_monitor_wan1_tx_yearly_gb
    cycle: yearly
    always_available: true

  unifi_network_monitor_wan2_rx_daily_gb:
    source: sensor.unifi_network_monitor_wan2_rx_total
    name: UniFi Network Monitor WAN2 RX Daily GB
    unique_id: unifi_network_monitor_wan2_rx_daily_gb
    cycle: daily
    always_available: true

  unifi_network_monitor_wan2_rx_weekly_gb:
    source: sensor.unifi_network_monitor_wan2_rx_total
    name: UniFi Network Monitor WAN2 RX Weekly GB
    unique_id: unifi_network_monitor_wan2_rx_weekly_gb
    cycle: weekly
    always_available: true

  unifi_network_monitor_wan2_rx_monthly_gb:
    source: sensor.unifi_network_monitor_wan2_rx_total
    name: UniFi Network Monitor WAN2 RX Monthly GB
    unique_id: unifi_network_monitor_wan2_rx_monthly_gb
    cycle: monthly
    always_available: true

  unifi_network_monitor_wan2_rx_yearly_gb:
    source: sensor.unifi_network_monitor_wan2_rx_total
    name: UniFi Network Monitor WAN2 RX Yearly GB
    unique_id: unifi_network_monitor_wan2_rx_yearly_gb
    cycle: yearly
    always_available: true

  unifi_network_monitor_wan2_tx_daily_gb:
    source: sensor.unifi_network_monitor_wan2_tx_total
    name: UniFi Network Monitor WAN2 TX Daily GB
    unique_id: unifi_network_monitor_wan2_tx_daily_gb
    cycle: daily
    always_available: true

  unifi_network_monitor_wan2_tx_weekly_gb:
    source: sensor.unifi_network_monitor_wan2_tx_total
    name: UniFi Network Monitor WAN2 TX Weekly GB
    unique_id: unifi_network_monitor_wan2_tx_weekly_gb
    cycle: weekly
    always_available: true

  unifi_network_monitor_wan2_tx_monthly_gb:
    source: sensor.unifi_network_monitor_wan2_tx_total
    name: UniFi Network Monitor WAN2 TX Monthly GB
    unique_id: unifi_network_monitor_wan2_tx_monthly_gb
    cycle: monthly
    always_available: true

  unifi_network_monitor_wan2_tx_yearly_gb:
    source: sensor.unifi_network_monitor_wan2_tx_total
    name: UniFi Network Monitor WAN2 TX Yearly GB
    unique_id: unifi_network_monitor_wan2_tx_yearly_gb
    cycle: yearly
    always_available: true

  unifi_network_monitor_internet_rx_daily_gb:
    source: sensor.unifi_network_monitor_internet_rx_total
    name: UniFi Network Monitor Internet RX Daily GB
    unique_id: unifi_network_monitor_internet_rx_daily_gb
    cycle: daily
    always_available: true

  unifi_network_monitor_internet_rx_weekly_gb:
    source: sensor.unifi_network_monitor_internet_rx_total
    name: UniFi Network Monitor Internet RX Weekly GB
    unique_id: unifi_network_monitor_internet_rx_weekly_gb
    cycle: weekly
    always_available: true

  unifi_network_monitor_internet_rx_monthly_gb:
    source: sensor.unifi_network_monitor_internet_rx_total
    name: UniFi Network Monitor Internet RX Monthly GB
    unique_id: unifi_network_monitor_internet_rx_monthly_gb
    cycle: monthly
    always_available: true

  unifi_network_monitor_internet_rx_yearly_gb:
    source: sensor.unifi_network_monitor_internet_rx_total
    name: UniFi Network Monitor Internet RX Yearly GB
    unique_id: unifi_network_monitor_internet_rx_yearly_gb
    cycle: yearly
    always_available: true

  unifi_network_monitor_internet_tx_daily_gb:
    source: sensor.unifi_network_monitor_internet_tx_total
    name: UniFi Network Monitor Internet TX Daily GB
    unique_id: unifi_network_monitor_internet_tx_daily_gb
    cycle: daily
    always_available: true

  unifi_network_monitor_internet_tx_weekly_gb:
    source: sensor.unifi_network_monitor_internet_tx_total
    name: UniFi Network Monitor Internet TX Weekly GB
    unique_id: unifi_network_monitor_internet_tx_weekly_gb
    cycle: weekly
    always_available: true

  unifi_network_monitor_internet_tx_monthly_gb:
    source: sensor.unifi_network_monitor_internet_tx_total
    name: UniFi Network Monitor Internet TX Monthly GB
    unique_id: unifi_network_monitor_internet_tx_monthly_gb
    cycle: monthly
    always_available: true

  unifi_network_monitor_internet_tx_yearly_gb:
    source: sensor.unifi_network_monitor_internet_tx_total
    name: UniFi Network Monitor Internet TX Yearly GB
    unique_id: unifi_network_monitor_internet_tx_yearly_gb
    cycle: yearly
    always_available: true

  unifi_network_monitor_internet_total_daily_gb:
    source: sensor.unifi_network_monitor_internet_total
    name: UniFi Network Monitor Internet Total Daily GB
    unique_id: unifi_network_monitor_internet_total_daily_gb
    cycle: daily
    always_available: true

  unifi_network_monitor_internet_total_weekly_gb:
    source: sensor.unifi_network_monitor_internet_total
    name: UniFi Network Monitor Internet Total Weekly GB
    unique_id: unifi_network_monitor_internet_total_weekly_gb
    cycle: weekly
    always_available: true

  unifi_network_monitor_internet_total_monthly_gb:
    source: sensor.unifi_network_monitor_internet_total
    name: UniFi Network Monitor Internet Total Monthly GB
    unique_id: unifi_network_monitor_internet_total_monthly_gb
    cycle: monthly
    always_available: true

  unifi_network_monitor_internet_total_yearly_gb:
    source: sensor.unifi_network_monitor_internet_total
    name: UniFi Network Monitor Internet Total Yearly GB
    unique_id: unifi_network_monitor_internet_total_yearly_gb
    cycle: yearly
    always_available: true

template:
  - sensor:
      - name: UniFi Network Monitor WAN Last Event Ago Pretty
        unique_id: unifi_network_monitor_wan_last_event_ago_pretty
        icon: mdi:clock-outline
        state: >
          {% set raw = state_attr('sensor.unifi_network_monitor_wan_last_event', 'time') %}
          {% set ts = as_timestamp(raw, default=none) if raw not in [none, '', 'unknown', 'unavailable'] else none %}
          {% if ts is none %}
            never
          {% else %}
            {% set seconds = (as_timestamp(now()) - ts) | int(0) %}
            {% if seconds < 60 %}
              gerade eben
            {% elif seconds < 3600 %}
              vor {{ (seconds / 60) | round(0, 'floor') | int }} Minuten
            {% elif seconds < 86400 %}
              vor {{ (seconds / 3600) | round(0, 'floor') | int }} Stunden
            {% else %}
              vor {{ (seconds / 86400) | round(0, 'floor') | int }} Tagen
            {% endif %}
          {% endif %}

sensor:
  - platform: history_stats
    name: UniFi Network Monitor Internet Uptime Today Percent
    unique_id: unifi_network_monitor_internet_uptime_today_percent
    entity_id: binary_sensor.unifi_network_monitor_internet_online
    state: "on"
    type: ratio
    start: "{{ today_at() }}"
    end: "{{ now() }}"
```

## Optional connectivity helper package

The integration itself monitors UniFi WAN state, traffic and webhooks. If you also want independent DNS, AdGuard DNS and HTTP checks, use:

```text
examples/packages/unifi_network_monitor_connectivity_helpers.yaml
```

Copy it to:

```text
/config/packages/internet_connectivity_helpers.yaml
```

The helper package does not poll the UniFi API directly. It only runs local DNS and HTTP checks and uses the integration entities for WAN state.

It creates compatibility entities such as:

```text
binary_sensor.internet_dns_external_ok
binary_sensor.internet_dns_adguard_ok
binary_sensor.internet_http_ok
binary_sensor.internet_fully_healthy
sensor.internet_health_state
sensor.internet_stability_score
sensor.internet_routing_mode_stable
counter.internet_failovers_today
```

The full helper package is included in the repository, but is intentionally not installed automatically.

## Webhook support

The integration exposes a Home Assistant webhook for UniFi Alarm Manager WAN events.

Check the webhook URL in the attributes of:

```text
sensor.unifi_network_monitor_wan_webhook_status
```

Webhook related entities:

```text
sensor.unifi_network_monitor_wan_event_history
sensor.unifi_network_monitor_wan_last_event
sensor.unifi_network_monitor_wan_webhook_status
sensor.unifi_network_monitor_wan1_outages_today
sensor.unifi_network_monitor_wan2_outages_today
sensor.unifi_network_monitor_wan_total_outages_today
button.unifi_network_monitor_clear_wan_event_history
```

The integration also fires these Home Assistant events:

```text
unifi_network_monitor_wan_event
unifi_network_monitor_notification
```

Treat the webhook ID like a secret. Anyone with that URL can send webhook data to Home Assistant.

## Example dashboard

An example dashboard view is included here:

```text
examples/dashboards/90_noc_unifi_network_monitor.yaml
```

It expects the integration entities and the Utility Meter helper package.

## Diagnostics

Diagnostics redact credentials, API keys, webhook IDs and SNMP community strings.

## License

MIT
