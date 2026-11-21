# MobilisTransit
Project of MobilisTransit for Master's Degree of Computer Science

# Set up SUMO

This section contains instructions for installing, running, and replicating the simulation files for **Mobilis Line 9** in Leiria, Portugal.

---

## 1. Installation and Run

1. Download the most recent version of SUMO from the official documentation: [Downloads - SUMO Documentation](https://sumo.dlr.de/docs/Downloads.php).
2. Run the installer and leave the default options enabled (or optionally check the boxes to install Python tool dependencies and `libsumo` automatically so you do not need to install them manually later).
3. Open **`sumo-gui`**, go to **File** $\rightarrow$ **Open Simulation...** (`Ctrl + O`), and select **`linha9.sumocfg`**.

### `linha9.sumocfg` Configuration

This configuration file is built specifically for Line 9:

```xml
<configuration>
    <input>
        <net-file value="leiria.net.xml.gz"/>
        <additional-files value="pt_vtypes.xml,gtfs_pt_stops.add.xml,gtfs_pt_vehicles_L9.add.xml"/>
    </input>
    <time>
        <begin value="25200"/>
        <end value="72000"/>
    </time>
</configuration>
```

> **Note:** The file `gtfs_pt_vehicles_L9.add.xml` is filtered from `gtfs_pt_vehicles.add.xml`. The original `gtfs_pt_vehicles.add.xml` containing all Mobilis routes is preserved for potential future expansion to other lines.

---

## 2. Replicating and Generating the Simulation Files

Follow the steps below to reproduce the network and public transport files from scratch.

### Step 1: Obtain the GTFS Data
1. Access the Portuguese National Access Point: [NAP - Portugal](https://nap-portugal.imt-ip.pt/).
2. Locate the Mobilis Leiria dataset, click on the provided Google Drive link, and download only the **`mobilis.zip`** file into your working directory.

### Step 2: Generate the Leiria Road Network
1. Open a terminal (CMD or PowerShell) in the folder where you want to generate the map files and run:
   ```bash
   python "%SUMO_HOME%\tools\osmWebWizard.py"
   ```
2. In the browser interface that opens, adjust the bounding box to cover the key locations along **Line 9** in Leiria:
   * **Av. Bernardo Pimenta, Leiria** (Terminal)
   * **Universidade de Leiria e Oeste | ESTG - Escola Superior de Tecnologia e Gestão**, P5MH+MJ, Campus 2 - Morro do Lena, R. do Alto Vieiro Apt 4163 Edifício D, 2411-901 Leiria
   * **McDonald's - Leiria Guimarota**

3. Click **Generate Scenario**. Once finished, locate the generated **`osm.net.xml.gz`** file, move it into the same folder as `mobilis.zip`, and rename it to **`leiria.net.xml.gz`**.

### Step 3: Convert GTFS Routes and Stops to SUMO Format
1. Check `calendar_dates.txt` (or `calendar.txt`) inside `mobilis.zip` to confirm a valid weekday date in `YYYYMMDD` format (for example, `20261123`).
2. Open PowerShell or CMD in the folder containing `mobilis.zip` and `leiria.net.xml.gz`, and execute:
   ```bash
   python "%SUMO_HOME%\tools\import\gtfs\gtfs2pt.py" -n leiria.net.xml.gz --gtfs mobilis.zip --date 20261123 --modes bus --vtype-output pt_vtypes.xml
   ```
   *(Note: If you decompressed the network file first, replace `leiria.net.xml.gz` with `leiria.net.xml`.)*

This generates `pt_vtypes.xml`, `gtfs_pt_stops.add.xml`, and `gtfs_pt_vehicles.add.xml` for all Mobilis routes in Leiria.

### Step 4: Filter Out Other Routes (Keep Only Line 9)
To isolate **Line 9** (`route_id` = `1010`), run the following command in the same terminal to strip all other routes from `gtfs_pt_vehicles.add.xml`:

```bash
python -c "import xml.etree.ElementTree as ET; t=ET.parse('gtfs_pt_vehicles.add.xml'); r=t.getroot(); [r.remove(e) for e in list(r) if not e.attrib.get('id','').startswith('1010-')]; t.write('gtfs_pt_vehicles_L9.add.xml', encoding='utf-8', xml_declaration=True)"
```

You can now load `linha9.sumocfg` in `sumo-gui` and run the simulation.
