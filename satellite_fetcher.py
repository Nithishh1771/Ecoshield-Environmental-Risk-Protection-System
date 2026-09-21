import requests
import urllib3
import xml.etree.ElementTree as ET
import json
import re
from datetime import datetime


# ============================================================
# ECO SHIELD
# FSI VAN AGNI - VIIRS SATELLITE HOTSPOT FETCHER
# ============================================================

# Disable the SSL warning caused by the Van Agni server
# certificate. We are still connecting to the official
# FSI Van Agni server.
urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


# ------------------------------------------------------------
# OFFICIAL FSI VAN AGNI WFS URL
# ------------------------------------------------------------

WFS_URL = (
    "https://vanagniportal.fsiforestfire.gov.in/cgi-bin/mapserv.exe"
    "?map=/ms4w/apps/fire/fire.map"
    "&service=WFS"
    "&version=1.1.0"
    "&request=GetFeature"
    "&typename=VIIRS_TODAY"
    "&outputformat=GML2"
)


# Output file
OUTPUT_FILE = "satellite_hotspots.json"


# ------------------------------------------------------------
# DOWNLOAD SATELLITE DATA
# ------------------------------------------------------------

def download_wfs_data():

    print()
    print("=" * 60)
    print("ECO SHIELD - SATELLITE FIRE HOTSPOT FETCHER")
    print("=" * 60)

    print()
    print("Connecting to FSI Van Agni...")
    print("Layer: VIIRS_TODAY")
    print()

    try:

        response = requests.get(
            WFS_URL,
            timeout=60,
            verify=False
        )

        response.raise_for_status()

        print("✅ Connected successfully")
        print("HTTP Status:", response.status_code)
        print(
            "Downloaded:",
            len(response.content),
            "bytes"
        )

        return response.content

    except requests.exceptions.RequestException as e:

        print()
        print("❌ Failed to download satellite data")
        print("Error:", e)

        return None


# ------------------------------------------------------------
# EXTRACT COORDINATES FROM GML
# ------------------------------------------------------------

def extract_coordinates(gml_data):

    print()
    print("Parsing GML data...")

    try:

        root = ET.fromstring(gml_data)

    except ET.ParseError as e:

        print("❌ XML/GML parsing error")
        print("Error:", e)

        return []

    hotspots = []

    # GML namespace
    GML = "http://www.opengis.net/gml"

    # --------------------------------------------------------
    # Find all feature members
    # --------------------------------------------------------

    feature_members = root.findall(
        ".//{%s}featureMember" % GML
    )

    print(
        "Feature members found:",
        len(feature_members)
    )

    # --------------------------------------------------------
    # Process every satellite hotspot
    # --------------------------------------------------------

    for index, feature_member in enumerate(
        feature_members,
        start=1
    ):

        coordinate_elements = feature_member.findall(
            ".//{%s}coordinates" % GML
        )

        if not coordinate_elements:
            continue

        all_coordinates = []

        # ----------------------------------------------------
        # Extract every coordinate pair
        # ----------------------------------------------------

        for element in coordinate_elements:

            text = element.text

            if not text:
                continue

            # Example:
            #
            # 23.821529,84.572209
            # 23.821529,84.570372
            #
            # Format:
            # latitude,longitude

            pairs = re.findall(
                r"(-?\d+(?:\.\d+)?),"
                r"(-?\d+(?:\.\d+)?)",
                text
            )

            for lat, lon in pairs:

                try:

                    latitude = float(lat)
                    longitude = float(lon)

                    # ------------------------------------------------
                    # Approximate India geographic boundary filter
                    # ------------------------------------------------

                    if (
                        6 <= latitude <= 38
                        and 67 <= longitude <= 98
                    ):

                        all_coordinates.append(
                            (
                                latitude,
                                longitude
                            )
                        )

                except ValueError:

                    continue

        # ----------------------------------------------------
        # Skip if no valid coordinates
        # ----------------------------------------------------

        if not all_coordinates:
            continue

        # ----------------------------------------------------
        # Calculate center point
        # ----------------------------------------------------

        average_latitude = (
            sum(
                point[0]
                for point in all_coordinates
            )
            / len(all_coordinates)
        )

        average_longitude = (
            sum(
                point[1]
                for point in all_coordinates
            )
            / len(all_coordinates)
        )

        # ----------------------------------------------------
        # Create hotspot record
        # ----------------------------------------------------

        hotspot = {

            "id": index,

            "source": "FSI_VAN_AGNI",

            "layer": "VIIRS_TODAY",

            "latitude": round(
                average_latitude,
                6
            ),

            "longitude": round(
                average_longitude,
                6
            ),

            "coordinate_count": len(
                all_coordinates
            )
        }

        hotspots.append(hotspot)

    return hotspots


# ------------------------------------------------------------
# SAVE SATELLITE HOTSPOTS
# ------------------------------------------------------------

def save_hotspots(hotspots):

    output = {

        "source": "FSI_VAN_AGNI",

        "layer": "VIIRS_TODAY",

        "retrieved_at": datetime.now().isoformat(),

        "count": len(hotspots),

        "hotspots": hotspots
    }

    try:

        with open(
            OUTPUT_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                output,
                file,
                indent=4
            )

        print()
        print("✅ Satellite data saved")
        print("File:", OUTPUT_FILE)

    except Exception as e:

        print()
        print("❌ Failed to save JSON file")
        print("Error:", e)


# ------------------------------------------------------------
# DISPLAY RESULTS
# ------------------------------------------------------------

def display_results(hotspots):

    print()
    print("=" * 60)
    print("SATELLITE HOTSPOT RESULTS")
    print("=" * 60)

    print()
    print(
        "Total hotspots extracted:",
        len(hotspots)
    )

    if not hotspots:

        print()
        print("⚠️ No hotspots were extracted.")

        return

    print()
    print("First 10 hotspots:")
    print("-" * 60)

    for hotspot in hotspots[:10]:

        print(
            f"#{hotspot['id']:03d}  "
            f"Lat: "
            f"{hotspot['latitude']:.6f}  "
            f"Lon: "
            f"{hotspot['longitude']:.6f}"
        )

    print("-" * 60)


# ------------------------------------------------------------
# MAIN PROGRAM
# ------------------------------------------------------------

def main():

    # Step 1:
    # Download official FSI satellite data

    gml_data = download_wfs_data()

    if gml_data is None:

        return

    # Step 2:
    # Extract hotspot coordinates

    hotspots = extract_coordinates(
        gml_data
    )

    # Step 3:
    # Save results

    save_hotspots(
        hotspots
    )

    # Step 4:
    # Display results

    display_results(
        hotspots
    )

    print()
    print("✅ Satellite extraction completed.")
    print()


# ------------------------------------------------------------
# PROGRAM START
# ------------------------------------------------------------

if __name__ == "__main__":

    main()