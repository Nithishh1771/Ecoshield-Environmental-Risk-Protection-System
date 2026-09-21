import json
import os
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


# ============================================================
# ECO SHIELD - MQTT CONFIGURATION
# (unchanged: same broker / port / topic as before)
# ============================================================

MQTT_BROKER = " 10.66.148.178"
MQTT_PORT = 1883
MQTT_TOPIC = "forest/fire"

# File used to store the latest ESP32 reading (unchanged path —
# risk_engine.py and /api/latest-sensor both still read this file)
LATEST_SENSOR_FILE = os.path.join(
    os.path.dirname(__file__),
    "latest_sensor_data.json"
)

# A reading is only considered "LIVE" if it arrived within this many
# seconds. Older than this and the dashboard should say WAITING FOR DATA.
LIVE_FRESHNESS_SECONDS = 15


# ============================================================
# THREAD-SAFE SHARED STATE
# ------------------------------------------------------------
# The MQTT network loop runs on a background thread, while Flask
# request threads (and the SSE generator) read this state. Every
# read/write goes through _lock to avoid race conditions.
# ============================================================

_lock = threading.Lock()
_latest_data = None        # last successfully parsed MQTT payload (dict)
_last_update_ts = None     # UTC datetime of the last successful message
_mqtt_connected = False    # current broker connection state

_client = None
_started = False           # guards against starting more than one listener
_update_callback = None    # optional fn(data) invoked on every new reading


def set_update_callback(callback):
    """
    Register a function to be called (with the latest data dict) every
    time a new, valid MQTT reading is processed. Used by app.py to push
    the reading out to connected SSE clients.
    """
    global _update_callback
    _update_callback = callback


def get_latest_data():
    """Thread-safe snapshot of the most recent sensor reading, or None."""
    with _lock:
        return dict(_latest_data) if _latest_data is not None else None


def get_last_update_ts():
    with _lock:
        return _last_update_ts


def is_connected():
    with _lock:
        return _mqtt_connected


def get_live_status():
    """
    Returns ('LIVE' | 'WAITING', age_seconds | None) based on how long ago
    the last reading was received. Never reports LIVE if nothing has been
    received yet or the broker connection is currently down.
    """
    with _lock:
        connected = _mqtt_connected
        last_ts = _last_update_ts

    if not connected or last_ts is None:
        return "WAITING", None

    age = (datetime.now(timezone.utc) - last_ts).total_seconds()
    if age <= LIVE_FRESHNESS_SECONDS:
        return "LIVE", age
    return "WAITING", age


def _set_latest(data):
    global _latest_data, _last_update_ts
    with _lock:
        _latest_data = data
        _last_update_ts = datetime.now(timezone.utc)


# ============================================================
# MQTT CONNECT / DISCONNECT
# ============================================================

def on_connect(client, userdata, flags, reason_code, properties=None):
    global _mqtt_connected

    if reason_code == 0:
        with _lock:
            _mqtt_connected = True

        print("[MQTT] Connected to broker")

        client.subscribe(MQTT_TOPIC)

        print(f"[MQTT] Subscribed to: {MQTT_TOPIC}")

    else:
        with _lock:
            _mqtt_connected = False

        print("[MQTT] Connection failed. Reason:", reason_code)


def on_disconnect(client, userdata, *args, **kwargs):
    global _mqtt_connected
    with _lock:
        _mqtt_connected = False
    print("[MQTT] Disconnected from broker")


# ============================================================
# MQTT MESSAGE
# ============================================================

def on_message(client, userdata, message):

    # ----------------------------------------------------
    # Read + parse MQTT message (never crash the listener)
    # ----------------------------------------------------
    try:
        payload = message.payload.decode()
    except Exception as e:
        print("[MQTT] Error decoding message payload:", e)
        return

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        print("[MQTT] Ignored malformed (invalid JSON) message:", e)
        return

    if not isinstance(data, dict):
        print("[MQTT] Ignored message: expected a JSON object")
        return

    try:
        probability = data.get("fire_probability", 0)
        fire = data.get("fire", 0)

        # ----------------------------------------------------
        # Build the latest reading — same fields as before, plus
        # a timestamp so the frontend can show "Last Updated".
        # ----------------------------------------------------
        latest_data = {
            "source": "ESP32_EDGE_AI",
            "latitude": 8.996939,
            "longitude": 77.862181,
            "mlx_temp": data.get("mlx_temp"),
            "dht_temp": data.get("dht_temp"),
            "humidity": data.get("humidity"),
            "mq2": data.get("mq2"),
            "flame": data.get("flame"),
            "fire_probability": probability,
            "fire": fire,
            "mq2_digital": data.get("mq2_digital"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # ----------------------------------------------------
        # Update in-memory state (thread-safe)
        # ----------------------------------------------------
        _set_latest(latest_data)

        # ----------------------------------------------------
        # Persist to disk too — unchanged behaviour, still what
        # /api/latest-sensor and risk_engine.py read from.
        # ----------------------------------------------------
        try:
            with open(LATEST_SENSOR_FILE, "w", encoding="utf-8") as file:
                json.dump(latest_data, file, indent=4)
        except Exception as e:
            print("[MQTT] Warning: could not write latest_sensor_data.json:", e)

        print("[MQTT] New sensor data received")

        # ----------------------------------------------------
        # Notify subscribers (e.g. Flask's SSE broadcaster)
        # ----------------------------------------------------
        if _update_callback is not None:
            try:
                _update_callback(latest_data)
            except Exception as e:
                print("[MQTT] Update callback error:", e)

    except Exception as e:
        # Catch-all so one bad/unexpected message never kills the listener
        print("[MQTT] Error processing MQTT message:", e)


# ============================================================
# START MQTT CLIENT (background thread — does not block Flask)
# ============================================================

def _run_forever():
    """
    Connects to the broker and runs the network loop. If the connection
    drops or fails, waits a few seconds and retries, forever, without
    ever blocking the Flask process (this runs on its own thread).
    """
    while True:
        try:
            print("[MQTT] Connecting to broker", MQTT_BROKER, MQTT_PORT)
            _client.connect(MQTT_BROKER, MQTT_PORT, 60)
            _client.loop_forever(retry_first_connection=True)
        except Exception as e:
            print("[MQTT] Connection error:", e, "- retrying in 5s")
        time.sleep(5)


def start_mqtt_client():
    """
    Starts exactly one background MQTT listener thread. Safe to call
    multiple times — only the first call actually starts anything, so
    app.py can call this at startup without risking duplicate MQTT
    subscribers to forest/fire.
    """
    global _client, _started

    with _lock:
        if _started:
            print("[MQTT] start_mqtt_client() called again — listener already running, skipping")
            return
        _started = True

    print("[MQTT] Starting EcoShield MQTT listener thread...")

    _client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    _client.on_connect = on_connect
    _client.on_disconnect = on_disconnect
    _client.on_message = on_message

    thread = threading.Thread(target=_run_forever, name="mqtt-listener", daemon=True)
    thread.start()
