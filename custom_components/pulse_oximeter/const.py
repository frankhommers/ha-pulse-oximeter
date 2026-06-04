"""Constants for the Pulse Oximeter (BLE) integration."""

DOMAIN = "pulse_oximeter"

# BLE Service UUIDs
PLX_SERVICE_UUID = "00001822-0000-1000-8000-00805f9b34fb"
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
DEVICE_INFO_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"

# BLE Characteristic UUIDs
PLX_CONTINUOUS_UUID = "00002a5f-0000-1000-8000-00805f9b34fb"
PLX_SPOT_CHECK_UUID = "00002a5e-0000-1000-8000-00805f9b34fb"
PLX_FEATURES_UUID = "00002a60-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
MANUFACTURER_NAME_UUID = "00002a29-0000-1000-8000-00805f9b34fb"
MODEL_NUMBER_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
FIRMWARE_REV_UUID = "00002a26-0000-1000-8000-00805f9b34fb"

PLATFORMS = ["sensor", "select"]

# Measurement session detection
SESSION_INACTIVITY_TIMEOUT = 30.0  # seconds without valid readings = session end
SESSION_MIN_READINGS = 5  # fewer valid readings = discard session

# Config entry options
CONF_PARTICIPANTS = "participants"
CONF_NOTIFY_TARGETS = "notify_targets"
CONF_NOTIFICATIONS_ENABLED = "notifications_enabled"

# Events (escape hatch for custom automations)
EVENT_MEASUREMENT_FINISHED = "pulse_oximeter_measurement_finished"
EVENT_MEASUREMENT_ASSIGNED = "pulse_oximeter_measurement_assigned"

# Service
SERVICE_ASSIGN_MEASUREMENT = "assign_measurement"

# Notifications
NOTIFY_ACTION_PREFIX = "PULSEOX"
MOBILE_APP_ACTION_EVENT = "mobile_app_notification_action"

# Dispatcher signal (format with entry_id)
SIGNAL_ASSIGNMENT_UPDATE = "pulse_oximeter_assignment_update_{entry_id}"

# User-facing texts, keyed by hass.config.language
TEXTS = {
    "en": {
        "notification_title": "Pulse Oximeter",
        "notification_message": "Measurement {time} — SpO2 {spo2}%, pulse {pulse} bpm. Yours?",
        "mine": "Mine",
        "not_mine": "Not mine",
        "faulty": "Faulty measurement",
        "unassigned": "Not assigned",
        "select_name": "Last measurement",
    },
    "nl": {
        "notification_title": "Saturatiemeter",
        "notification_message": "Meting {time} — SpO2 {spo2}%, pols {pulse} bpm. Voor jou?",
        "mine": "Voor mij",
        "not_mine": "Niet voor mij",
        "faulty": "Foutieve meting",
        "unassigned": "Niet toegewezen",
        "select_name": "Laatste meting",
    },
}


def get_texts(language: str) -> dict[str, str]:
    """Return UI texts for a language, falling back to English."""
    return TEXTS.get(language, TEXTS["en"])
