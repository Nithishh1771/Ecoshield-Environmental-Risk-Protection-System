from flask import Flask, jsonify, render_template, redirect, url_for, Response
import json
import os
import queue
import threading

from risk_engine import calculate_regional_risk
import mqtt_client


app = Flask(__name__)


# ============================================================
# LIVE MQTT DATA — SSE BROADCAST
# ------------------------------------------------------------
# mqtt_client.py owns the single MQTT subscription and the actual
# thread-safe "latest reading" state. Here we just keep a list of
# per-browser-tab queues so every connected dashboard tab receives
# each new reading as it arrives (see /api/live-stream below).
# ============================================================

_sse_subscribers = []
_sse_subscribers_lock = threading.Lock()


def _broadcast_to_sse_clients(data):
    with _sse_subscribers_lock:
        for q in _sse_subscribers:
            q.put(data)


mqtt_client.set_update_callback(_broadcast_to_sse_clients)


# ============================================================
# PAGE ROUTES
# ============================================================

@app.route("/")
def home():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", active="dashboard")


@app.route("/live-map")
def live_map():
    return render_template("live_map.html", active="live-map")


@app.route("/satellite")
def satellite():
    return render_template("satellite.html", active="satellite")


@app.route("/sensors")
def sensors():
    return render_template("sensors.html", active="sensors")


@app.route("/risk-analysis")
def risk_analysis():
    return render_template("risk_analysis.html", active="risk-analysis")


@app.route("/system-status")
def system_status():
    return render_template("system_status.html", active="system-status")


# ============================================================
# SYSTEM STATUS
# ============================================================

@app.route("/api/status")
def status():

    return jsonify({
        "system": "EcoShield",
        "status": "online"
    })


# ============================================================
# SATELLITE HOTSPOTS API
# ============================================================

@app.route("/api/satellite-hotspots")
def satellite_hotspots():

    # Path of satellite JSON file
    file_path = os.path.join(
        os.path.dirname(__file__),
        "satellite_hotspots.json"
    )

    # Check whether satellite file exists
    if not os.path.exists(file_path):

        return jsonify({
            "success": False,
            "error": "satellite_hotspots.json not found",
            "hotspots": []
        }), 404

    try:

        # Open satellite JSON file
        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        # Return satellite data
        return jsonify({
            "success": True,

            "source": data.get(
                "source",
                "FSI_VAN_AGNI"
            ),

            "layer": data.get(
                "layer",
                "VIIRS_TODAY"
            ),

            "retrieved_at": data.get(
                "retrieved_at"
            ),

            "count": data.get(
                "count",
                len(data.get("hotspots", []))
            ),

            "hotspots": data.get(
                "hotspots",
                []
            )
        })

    except json.JSONDecodeError:

        return jsonify({
            "success": False,
            "error": "Invalid JSON format",
            "hotspots": []
        }), 500

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e),
            "hotspots": []
        }), 500


# ============================================================
# REGIONAL FIRE RISK API
# ============================================================

@app.route("/api/regional-risk")
def regional_risk():

    try:

        # Calculate regional fire risk
        result = calculate_regional_risk()

        return jsonify(result)

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================
# LATEST SENSOR READING API
# (new — exposes the raw ESP32 fields that /api/regional-risk
# does not include, for the Sensor Network page)
# ============================================================

@app.route("/api/latest-sensor")
def latest_sensor():

    file_path = os.path.join(
        os.path.dirname(__file__),
        "latest_sensor_data.json"
    )

    if not os.path.exists(file_path):

        return jsonify({
            "success": False,
            "error": "latest_sensor_data.json not found"
        }), 404

    try:

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        return jsonify({
            "success": True,
            **data
        })

    except json.JSONDecodeError:

        return jsonify({
            "success": False,
            "error": "Invalid JSON format"
        }), 500

    except Exception as e:

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================
# LIVE MQTT DATA API (new — real-time dashboard support)
# ============================================================

@app.route("/api/live-data")
def live_data():
    """
    Returns the latest MQTT reading as JSON, plus connection/freshness
    info. Source of truth is mqtt_client's in-memory state (kept up to
    date by the background MQTT thread) — never fabricated values.
    """

    data = mqtt_client.get_latest_data()
    live_status, age_seconds = mqtt_client.get_live_status()

    if data is None:
        return jsonify({
            "success": False,
            "error": "No sensor reading received yet",
            "mqtt_connected": mqtt_client.is_connected(),
            "live_status": live_status
        }), 404

    return jsonify({
        "success": True,
        **data,
        "mqtt_connected": mqtt_client.is_connected(),
        "live_status": live_status,
        "age_seconds": age_seconds
    })


@app.route("/api/live-stream")
def live_stream():
    """
    Server-Sent Events stream. Pushes a new event only when the MQTT
    listener receives a new, valid reading on forest/fire — nothing is
    sent on a timer, so idle periods don't spam the connection.
    """

    def event_stream():
        client_queue = queue.Queue()

        with _sse_subscribers_lock:
            _sse_subscribers.append(client_queue)

        try:
            # Send whatever we already have immediately, so a freshly
            # opened tab doesn't sit empty until the next MQTT message.
            current = mqtt_client.get_latest_data()
            if current is not None:
                yield f"data: {json.dumps(current)}\n\n"

            while True:
                # Blocks (per-connection, on its own request thread)
                # until the MQTT thread pushes new data — no polling.
                data = client_queue.get()
                yield f"data: {json.dumps(data)}\n\n"
                print("[LIVE] Dashboard update sent")

        finally:
            with _sse_subscribers_lock:
                if client_queue in _sse_subscribers:
                    _sse_subscribers.remove(client_queue)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


# ============================================================
# RUN FLASK SERVER
# ============================================================

if __name__ == "__main__":

    # Start the single background MQTT listener (subscribes to
    # forest/fire) before serving requests. Guarded against Flask's
    # debug-mode reloader (which forks a second process) so we never
    # end up with two MQTT subscribers on the same topic.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        mqtt_client.start_mqtt_client()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
        threaded=True  # required so multiple SSE tabs + normal
                        # requests can all be served concurrently
    )
