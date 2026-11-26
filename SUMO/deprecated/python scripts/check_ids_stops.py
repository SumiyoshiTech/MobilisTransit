import xml.etree.ElementTree as ET

# 1. Collect stop IDs used by Line 9
l9_tree = ET.parse("gtfs_pt_vehicles_L9.add.xml")
l9_stop_ids = {
    stop.attrib["busStop"]
    for stop in l9_tree.getroot().findall(".//stop")
}

# 2. Print only matching stops from gtfs_pt_stops.add.xml
stops_tree = ET.parse("gtfs_pt_stops.add.xml")
for stop in stops_tree.getroot().findall("busStop"):
    stop_id = stop.attrib["id"]
    if stop_id in l9_stop_ids:
        stop_name = stop.attrib.get("name", "No name")
        print(f"{stop_id} -> {stop_name}")