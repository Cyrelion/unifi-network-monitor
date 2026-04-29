"""Constants for UniFi Network Monitor."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "unifi_network_monitor"
NAME = "UniFi Network Monitor"
VERSION = "1.0.2"

PLATFORMS = ["sensor", "binary_sensor", "button"]

DEFAULT_HOST = "192.168.1.1"
DEFAULT_SITE = "default"
DEFAULT_SITE_NAME = "Default"
DEFAULT_TELEPORT_PREFIX = "192.168.2.0/24"
DEFAULT_TELEPORT_CLIENT_MATCHERS = ""
DEFAULT_VERIFY_SSL = False
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_TIMEOUT = 20
DEFAULT_CACHE_MAX_AGE = 180

DEFAULT_SNMP_ENABLED = False
DEFAULT_SNMP_PORT = 161
DEFAULT_SNMP_TIMEOUT = 4
DEFAULT_WAN1_RX_OID = "1.3.6.1.2.1.31.1.1.1.6.3"
DEFAULT_WAN1_TX_OID = "1.3.6.1.2.1.31.1.1.1.10.3"
DEFAULT_WAN2_RX_OID = "1.3.6.1.2.1.31.1.1.1.6.5"
DEFAULT_WAN2_TX_OID = "1.3.6.1.2.1.31.1.1.1.10.5"

DEFAULT_WEBHOOK_ENABLED = True
DEFAULT_WEBHOOK_LOCAL_ONLY = False
DEFAULT_WEBHOOK_HISTORY_LIMIT = 20

CONF_SITE = "site"
CONF_SITE_NAME = "site_name"
CONF_API_KEY = "api_key"
CONF_TELEPORT_PREFIX = "teleport_prefix"
CONF_TELEPORT_CLIENT_MATCHERS = "teleport_client_matchers"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_CACHE_MAX_AGE = "cache_max_age"
CONF_SNMP_ENABLED = "snmp_enabled"
CONF_SNMP_HOST = "snmp_host"
CONF_SNMP_COMMUNITY = "snmp_community"
CONF_SNMP_PORT = "snmp_port"
CONF_SNMP_TIMEOUT = "snmp_timeout"
CONF_WAN1_RX_OID = "wan1_rx_oid"
CONF_WAN1_TX_OID = "wan1_tx_oid"
CONF_WAN2_RX_OID = "wan2_rx_oid"
CONF_WAN2_TX_OID = "wan2_tx_oid"
CONF_WEBHOOK_ENABLED = "webhook_enabled"
CONF_WEBHOOK_ID = "webhook_id"
CONF_WEBHOOK_LOCAL_ONLY = "webhook_local_only"

UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

ATTR_META = "meta"
ATTR_DATA = "data"
ATTR_RAW = "raw"
