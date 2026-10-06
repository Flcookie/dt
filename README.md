# Digital Twin Platform

Repository: [github.com/Flcookie/digital-twin-platform](https://github.com/Flcookie/digital-twin-platform)

Local checkout may still use the folder name `lego-factory`. **Do not commit passwords** (`config.json`, `.env`).

A Streamlit console for the MOTOWN / MT-EMS-PL multi-station assembly line: live control and KPI monitoring over MQTT, event persistence in Neo4j, history replay, part trace / conformance, a Plotly digital twin floor, and offline What-if sensitivity runs via Arena/SIMAN.

## Architecture (short)

```text
mt-ems-pl controllers  --MQTT events-->  main_service  --KPI MQTT-->  Streamlit
                              |                            |
                              +--Neo4j write---------------+--Neo4j query
What-if:  model/Config.txt + Input.txt  ->  siman.exe  ->  Output.txt  ->  charts
```

## Features

| Area | What you get |
|------|----------------|
| Control Panel | Live / History mode, start-stop line & programs, upload code/config |
| KPI Dashboard | System / stage / station KPIs and trends from `main_service` |
| History | Neo4j sessions, CSV import/export, session replay (sidecar KPI, not MQTT bus) |
| Part Trace / Conformance | Paths, flow type, conformance labels |
| Digital Twin | Factory layout + coordinated Part Trace |
| What-if Analysis | Sweep WIP / buffer capacities; median over replications |

Detailed design notes: [`report3.md`](report3.md).

## Quick start (software)

1. Copy `config.example.json` → `config.json` (and optionally `config_local.json` for replay).
2. Copy `.env.example` → `.env` and set `NEO4J_PASSWORD` / `SSH_PASSWORD` if you use env overrides.
3. Start Neo4j locally (`bolt://localhost:7687`) so Desktop’s DBMS is **running**, not only open.
4. Install and run:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Backend (event buffer, Neo4j write, KPI publish):

```bash
run_main.bat
# or: set CONFIG_FILE=config.json && python main_service.py
```

Frontend:

```bash
run_streamlit.bat
# or: run_web.bat   (same as run_streamlit)
# or: set CONFIG_FILE=config.json && python -m streamlit run streamlit_app/app.py
```

Open the Local URL printed by Streamlit (often `http://localhost:8501`). If that port is busy, pass `--server.port 8502` on the `streamlit run` command.

### What-if (Arena)

Requires Rockwell Arena `siman.exe` (override with `ARENA_SIMAN_EXE` if needed) and a work folder with:

```text
model/model.p
model/Input.txt      # 7 lines: WIP Limit + stage buffers
model/Config.txt     # ReplicasNum, WarmUp, SimLength
```

Page: **What-if Analysis** — set Parameter / From / To / Step / Replications, then Run. Each point runs under `model/runs/pt_*`. Scrap on that page is an Arena **throughput rate**, not the Live KPI scrap **ratio** (see `report3.md` §7.6 / §11.8).

## Repository layout (main)

| Path | Role |
|------|------|
| `streamlit_app/app.py` | Streamlit entry (Home / Control Panel) |
| `streamlit_app/pages/` | KPI, History, Part Trace, Conformance, Digital Twin, What-if |
| `streamlit_app/ui/` | UI panels and shell |
| `streamlit_app/services/` | MQTT, Neo4j queries, recording, line control helpers |
| `streamlit_app/twin/` | Factory floor Plotly sim / layout cache |
| `streamlit_app/part_track/` | Flow / conformance / part models |
| `streamlit_app/whatif/` | Arena SIMAN parameter sweep |
| `main_service.py` / `event_pipeline.py` / `kpi_calculator.py` | Live ingest + KPI |
| `mt-ems-pl/` | Controller code uploaded to the line (local; not always in git) |
| `model/` | Arena What-if model and inputs |
| `report3.md` | Full technical report |
| `doc/` | Local notes / older drafts (gitignored) |

Do not commit `config.json`, `.env`, `event-logs/`, `*.pid`, or control-panel state JSON. A generated `README.pdf` is optional and gitignored.

---

# **System Setup and Execution Guide**

Physical lab setup (controllers, Wi-Fi, MobaXterm, upload scripts) continues below.

## **1. Prerequisites**

### **1.1. Install Required Software**

Ensure that the following software is installed on your system:

- **PyCharm Community Edition** ([Download](https://www.jetbrains.com/pycharm/download/?section=windows))

  During installation, enable all available options.
  
  <img src="images/1-1-1.png" alt="" style="width:60%;"/>

- **MobaXterm Free Version** ([Download](https://mobaxterm.mobatek.net/download.html))

  Note: MobaXterm is compatible only with Windows. MacOS users may need alternative solutions.

### **1.2. Download and Prepare the Scripts**

1. Download the required scripts from WeBeep.

2. Extract the downloaded archive.

3. Move all extracted files into a dedicated local folder (e.g., `lego-factory`).

   <img src="images/1-2-1.png" alt="" style="width:15%;"/>

### **1.3. Create a Python Project in PyCharm**

1. Right-click the `lego-factory` folder and select **Show more options > Open Folder as PyCharm Community Edition
   Project**.

   <img src="images/1-3-1.png" alt="" style="width:40%;"/>

2. When prompted, trust the project in PyCharm.

   <img src="images/1-3-2.png" alt="" style="width:60%;"/>

3. PyCharm will automatically install Python and dependencies based on the `requirements.txt` file. Click **OK** when
   prompted. Note: You may need to select manually the interpreter from **File > Settings > Python > Interpreter**

   <img src="images/1-3-3.png" alt="" style="width:60%;"/>

4. Wait for the Python interpreter setup to complete (duration depends on internet speed).

5. Once completed, the Python interpreter will be visible in the lower-right corner of PyCharm.

<img src="images/1-3-4.png" alt="" style="width:100%;"/>

*Note:* It might be necessary to install the dependencies specified in `requirements.txt` manually, searching for them
from the **Python Packages** menu on the left-side bar.

### **1.4. Configure MobaXterm**

Consider the **Five-Station System** for example.

1. In PyCharm, navigate to the `g1-5s-pl` folder.

2. Locate `sessions.mxtsessions`, right-click, and copy its absolute path.

<img src="images/1-4-1.png" alt="" style="width:100%;"/>

3. In MobaXterm, right-click **User sessions** and select **Import sessions from file**.

   <img src="images/1-4-2.png" alt="" style="width:60%;"/>

4. Paste the copied path into the file explorer and select `sessions.mxtsessions`.

   <img src="images/1-4-3.png" alt="" style="width:60%;"/>

5. The `G1-5S-PL` folder should now appear in MobaXterm.

   <img src="images/1-4-4.png" alt="" style="width:60%;"/>

*Note:* These configuration steps need to be performed only once on your PC.

## 2. Execution Steps

### **2.1. Power On Controllers**

For the **Five-Station System**:

1. Press the center button on each controller.

2. Wait until the controllers boot up and the LED turns green.

<img src="images/2-1-1.png" alt="" style="width:100%;"/>

*Note:* No actions are required for the **Two-Station System** and **Multi-Loop System**. 

### **2.2. Connect to the Wi-Fi Network**

Select the appropriate network based on your system:

- **Five-Station System:**
  - Network: `THE FACTORY - ROUTER 1`
  - Password: `legofactory`
- **Two-Station System:**
  - Network: `THE FACTORY - ROUTER 2`
  - Password: `legofactory`
- **Multi-Loop System:**
  - Network: `THE FACTORY - ROUTER 3`
  - Password: `legofactory`

  <img src="images/2-2-1.png" alt="" style="width:40%;"/>

### **2.3. Set Up System Configuration**

Each system has a configuration file (e.g., `g1-5s-pl/config.json`). Copy the file from the corresponding folder into
the root folder of the PyCharm project.

Consider the **Five-Station System** for example:

1. Right-click `config.json` in the `g1-5s-pl` folder and select **Copy**.

<img src="images/2-3-1.png" alt="" style="width:100%;"/>

2. Right-click the root folder of the PyCharm project and select **Paste**.

<img src="images/2-3-2.png" alt="" style="width:100%;"/>

3. Specify the initial WIPs and log folder in the `config.json` file.

<img src="images/2-3-3.png" alt="" style="width:100%;"/>

### **2.4. Upload Components Code and Configuration**

Each component has at least one script file (e.g., `g1-5s-pl/station.py`) and at least one configuration file (e.g.,
`g1-5s-pl/s1/config.json`). To make sure that your code and configuration take effect, run the `upload_code.py` and
`upload_config.py` scripts in PyCharm to upload them to the controllers.

<img src="images/2-4-1.png" alt="" style="width:100%;"/>

<img src="images/2-4-2.png" alt="" style="width:100%;"/>

### **2.5. Open Controller Sessions**

1. Open MobaXterm.

2. Navigate to the `G1-5S-PL`, `G2-2S-PL` or `MT-EMS-PL` folder depending on your system.

3. Double-click each session to open it except `THE-FACTORY-PC1` and `broker`, which serve as MQTT brokers.

4. The first time a session is opened, enter the password: `maker`, then press `Enter` after. 

5. Follow the on-screen prompts to save the password in MobaXterm.

<img src="images/2-5-1.png" alt="" style="width:100%;"/>

<img src="images/2-5-2.png" alt="" style="width:100%;"/>

<img src="images/2-5-3.png" alt="" style="width:100%;"/>

*Recommendation:* Open sessions sequentially.

### **2.6. Activate Component Programs**

#### **2.6.1. Five-Station System**

1. Activate the program of each gate (`G-EV3`):

   Type the following command and press `Enter` after to execute it:

   ```bash
   python3 gate.py
   ```

<img src="images/2-6-1.png" alt="" style="width:100%;"/>

2. Activate the program of each station (from `S1-EV3` to `S5-EV3`):

   Type the following command and press `Enter` after to execute it:

   ```bash
   python3 station.py
   ```

<img src="images/2-6-2.png" alt="" style="width:100%;"/>

#### **2.6.2. Two-Station System**

1. Activate the program of each station (`S1-RPi` and `S2-RPi`):

   Type the following two commands and press `Enter` after to execute them one by one:

   ```bash
   cd Python/
   python3 station.py
   ```

<img src="images/2-6-3.png" alt="" style="width:100%;"/>

#### **2.6.3. Multi-Loop System**

1. Activate the program of each corner (`corner1` and `corner2`):

   Type the following two commands and press `Enter` after to execute them one by one:

   ```bash
   cd Python/
   python3 corner.py
   ```

<img src="images/2-6-4.png" alt="" style="width:100%;"/>

2. Activate the program of each driver (from `driver1` to `driver4`):

   Type the following two commands and press `Enter` after to execute them one by one:

   ```bash
   cd Python/
   python3 driver.py
   ```

<img src="images/2-6-5.png" alt="" style="width:100%;"/>

3. Activate the program of each splitter (`splitter5`):

   Type the following two commands and press `Enter` after to execute them one by one:

   ```bash
   cd Python/
   python3 splitter.py
   ```

<img src="images/2-6-6.png" alt="" style="width:100%;"/>

4. Activate the program of each splitter pair (`splitters12` and `splitters34`):

   Type the following two commands and press `Enter` after to execute them one by one:

   ```bash
   cd Python/
   python3 splitter_pair.py
   ```

<img src="images/2-6-7.png" alt="" style="width:100%;"/>

5. Activate the program of each blocking station (`station11`, `station31` and `station61`):

   Type the following two commands and press `Enter` after to execute them one by one:

   ```bash
   cd Python/
   python3 block_station.py
   ```

<img src="images/2-6-8.png" alt="" style="width:100%;"/>

6. Activate the program of each non-blocking station (`station21`, `station22`, `station41`, `station51`, `station52`
   and `station71`):

   Type the following two commands and press `Enter` after to execute them one by one:

   ```bash
   cd Python/
   python3 nonblock_station.py
   ```

<img src="images/2-6-9.png" alt="" style="width:100%;"/>

### **2.7. Start Recording Events**

Run the `record_events.py` script in PyCharm to start recording events.

<img src="images/2-7-1.png" alt="" style="width:100%;"/>

*Note:* This Python script will keep running until stopped manually.

### **2.8. Start the System**

Run the `start_system.py` script in PyCharm to start the system.

<img src="images/2-8-1.png" alt="" style="width:100%;"/>

### **2.9. Stop the System**

Run the `stop_system.py` script in PyCharm to stop the system.

<img src="images/2-9-1.png" alt="" style="width:100%;"/>

### **2.10 Stop Recording Events**

Stop the `record_events.py` script in PyCharm to stop recording events.

<img src="images/2-10-1.png" alt="" style="width:100%;"/>

*Note:* The event log will be saved to a `.csv` file in the specified folder.

### **2.11 Deactivate Component Programs**

To deactivate the program of each component, press `Ctrl + C` in the corresponding session of MobaXterm.

<img src="images/2-11-1.png" alt="" style="width:100%;"/>

<img src="images/2-11-2.png" alt="" style="width:100%;"/>

### **2.12 Shut Down Controllers**

Run the `shutdown.py` script in PyCharm to shut down the controllers.

<img src="images/2-12-1.png" alt="" style="width:100%;"/>

### **2.13 Web Dashboard (Streamlit)**

For real-time KPI monitoring via browser:

1. Install dependencies: `pip install -r requirements.txt`
2. **Terminal 1:** Run `python main_service.py` (or start **main_service** from Streamlit **01 实时监控**)
3. **Terminal 2:** Run `python -m streamlit run streamlit_app/app.py` (default URL: **http://localhost:8501**, opens **01** directly)

**Architecture note (形态 B):** keep **two processes** — `main_service` (events, Neo4j, KPI publish) and **Streamlit** (UI, MQTT control, queries). Streamlit does not replace `main_service`.

The app includes real-time KPI, history replay, part trace, and controls (physical line, recording, Neo4j, uploads).

**Quick run (Windows):** Use `run_main.bat` and `run_web.bat` in separate terminals.

**Detailed guide (Chinese):** See `运行指南.md` for full setup (note: some steps may still mention the old FastAPI port 8000).
