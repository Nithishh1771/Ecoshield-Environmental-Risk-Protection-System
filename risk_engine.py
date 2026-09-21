import json
import math
import os


# ============================================================
# ECO SHIELD
# REGIONAL FIRE RISK ENGINE
# ============================================================

BASE_DIR = os.path.dirname(__file__)

SENSOR_FILE = os.path.join(
    BASE_DIR,
    "latest_sensor_data.json"
)

SATELLITE_FILE = os.path.join(
    BASE_DIR,
    "satellite_hotspots.json"
)


# ============================================================
# ESP32 SENSOR LOCATION
# ============================================================

SENSOR_LATITUDE = 8.996939
SENSOR_LONGITUDE = 77.862181

SENSOR_RADIUS_KM = 10


# ============================================================
# SATELLITE REGIONAL GRID
# ============================================================

GRID_SIZE_DEGREES = 0.5


# ============================================================
# RISK LEVEL
# ============================================================

def get_risk_level(risk):

    if risk < 25:
        return "LOW"

    elif risk < 50:
        return "MODERATE"

    elif risk < 75:
        return "HIGH"

    else:
        return "CRITICAL"


# ============================================================
# DISTANCE
# ============================================================

def calculate_distance_km(
    lat1,
    lon1,
    lat2,
    lon2
):

    earth_radius = 6371.0

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)

    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        +
        math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return earth_radius * c


# ============================================================
# LOAD JSON
# ============================================================

def load_json(file_path):

    if not os.path.exists(file_path):
        return None

    try:

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception as e:

        print(
            "Error reading:",
            file_path
        )

        print(e)

        return None


# ============================================================
# SENSOR REGION SATELLITE EVIDENCE
# ============================================================

def calculate_sensor_satellite_evidence(
    hotspots
):

    nearby_hotspots = []

    for hotspot in hotspots:

        latitude = hotspot.get("latitude")
        longitude = hotspot.get("longitude")

        if latitude is None or longitude is None:
            continue

        distance = calculate_distance_km(
            SENSOR_LATITUDE,
            SENSOR_LONGITUDE,
            latitude,
            longitude
        )

        if distance <= SENSOR_RADIUS_KM:

            nearby_hotspots.append({

                "id": hotspot.get("id"),

                "latitude": latitude,

                "longitude": longitude,

                "distance_km": round(
                    distance,
                    3
                )

            })


    count = len(nearby_hotspots)

    if count == 0:
        evidence = 0

    elif count == 1:
        evidence = 40

    elif count == 2:
        evidence = 60

    elif count == 3:
        evidence = 75

    else:
        evidence = 90


    return evidence, nearby_hotspots


# ============================================================
# CREATE SATELLITE REGIONS
# ============================================================

def create_satellite_regions(hotspots):

    regions = {}


    for hotspot in hotspots:

        latitude = hotspot.get("latitude")
        longitude = hotspot.get("longitude")

        if latitude is None or longitude is None:
            continue


        # ----------------------------------------------------
        # Assign detection to geographic grid
        # ----------------------------------------------------

        grid_lat = math.floor(
            latitude / GRID_SIZE_DEGREES
        ) * GRID_SIZE_DEGREES

        grid_lon = math.floor(
            longitude / GRID_SIZE_DEGREES
        ) * GRID_SIZE_DEGREES


        key = (
            round(grid_lat, 2),
            round(grid_lon, 2)
        )


        if key not in regions:

            regions[key] = []

        regions[key].append(hotspot)


    satellite_regions = []


    for key, region_hotspots in regions.items():

        grid_lat, grid_lon = key

        detection_count = len(
            region_hotspots
        )


        # ----------------------------------------------------
        # Satellite-only evidence
        # ----------------------------------------------------

        if detection_count == 1:

            risk_score = 40

        elif detection_count == 2:

            risk_score = 55

        elif detection_count == 3:

            risk_score = 65

        elif detection_count <= 5:

            risk_score = 75

        else:

            risk_score = 90


        risk_level = get_risk_level(
            risk_score
        )


        # ----------------------------------------------------
        # Calculate center of detections
        # ----------------------------------------------------

        center_latitude = (
            sum(
                h["latitude"]
                for h in region_hotspots
            )
            /
            detection_count
        )

        center_longitude = (
            sum(
                h["longitude"]
                for h in region_hotspots
            )
            /
            detection_count
        )


        satellite_regions.append({

            "region_id":
                f"SAT-{len(satellite_regions) + 1:03d}",

            "center": {

                "latitude":
                    round(
                        center_latitude,
                        6
                    ),

                "longitude":
                    round(
                        center_longitude,
                        6
                    )

            },

            "detection_count":
                detection_count,

            "risk_score":
                risk_score,

            "risk_level":
                risk_level,

            "mode":
                "SATELLITE_ONLY"

        })


    # Highest-risk regions first

    satellite_regions.sort(
        key=lambda x: (
            x["risk_score"],
            x["detection_count"]
        ),
        reverse=True
    )


    return satellite_regions


# ============================================================
# MAIN REGIONAL RISK
# ============================================================

def calculate_regional_risk():

    sensor_data = load_json(
        SENSOR_FILE
    )

    satellite_data = load_json(
        SATELLITE_FILE
    )


    if sensor_data is None:

        return {

            "success": False,

            "error":
                "latest_sensor_data.json not found"

        }


    if satellite_data is None:

        return {

            "success": False,

            "error":
                "satellite_hotspots.json not found"

        }


    hotspots = satellite_data.get(
        "hotspots",
        []
    )


    # ========================================================
    # SENSOR REGION
    # ========================================================

    edge_probability = sensor_data.get(
        "fire_probability",
        0
    )

    edge_probability_percent = (
        edge_probability * 100
    )


    satellite_evidence, nearby_hotspots = (
        calculate_sensor_satellite_evidence(
            hotspots
        )
    )


    combined_risk = (
        edge_probability_percent * 0.70
        +
        satellite_evidence * 0.30
    )


    combined_risk = min(
        100,
        max(0, combined_risk)
    )


    sensor_region = {

        "mode":
            "EDGE_AI_PLUS_SATELLITE",

        "center": {

            "latitude":
                SENSOR_LATITUDE,

            "longitude":
                SENSOR_LONGITUDE

        },

        "radius_km":
            SENSOR_RADIUS_KM,

        "edge_ai_probability":
            round(
                edge_probability_percent,
                2
            ),

        "satellite_nearby_detections":
            len(
                nearby_hotspots
            ),

        "satellite_evidence":
            satellite_evidence,

        "risk_score":
            round(
                combined_risk,
                2
            ),

        "risk_level":
            get_risk_level(
                combined_risk
            )

    }


    # ========================================================
    # SATELLITE-ONLY REGIONS
    # ========================================================

    satellite_regions = (
        create_satellite_regions(
            hotspots
        )
    )


    # ========================================================
    # FINAL RESULT
    # ========================================================

    return {

        "success": True,

        "sensor_region":
            sensor_region,

        "satellite_only_regions":
            satellite_regions,

        "summary": {

            "total_satellite_detections":
                len(hotspots),

            "satellite_regions":
                len(satellite_regions),

            "sensor_region_mode":
                "EDGE_AI_PLUS_SATELLITE"

        }

    }


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    print()

    print("=" * 60)
    print(
        "ECO SHIELD - REGIONAL FIRE RISK ENGINE"
    )
    print("=" * 60)

    result = calculate_regional_risk()

    print()

    print(
        json.dumps(
            result,
            indent=4
        )
    )

    print()

    print(
        "✅ Regional risk calculation completed."
    )

    print()