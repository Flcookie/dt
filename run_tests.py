# run_tests.py - Run all tests and report
#
# Each group runs under pytest so every test function is executed (running a test file as a
# plain script only executes its optional __main__ block). pytest must be installed; the
# streamlit_app UI tests also need streamlit (requirements.txt) and are skipped without it.
# The Neo4j integration group runs only when NEO4J_TEST_URI points at a disposable test
# database (see test_floor_events_neo4j.py); otherwise it is reported as skipped (-rs).

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

tests = [
    # No dedicated event_buffer test file exists; EventBuffer is exercised through the
    # pipeline tests here and the plant-log regression in test_kpi_calculator.py.
    ("event pipeline / buffer", ["test_event_pipeline.py"]),
    ("KPI calculator", ["test_kpi_calculator.py"]),
    ("process stages", ["test_process_stages.py"]),
    ("replay scripts", ["test_replay_direct.py"]),
    (
        "streamlit_app",
        [
            "test_mqtt_backend_replay.py",
            "test_history_panel_ui.py",
            "test_part_trace_panel_ui.py",
            "test_home_ui.py",
            "test_factory_floor_sim.py",
            "test_kpi_display_ui.py",
        ],
    ),
    ("Neo4j integration (opt-in: NEO4J_TEST_URI)", ["test_floor_events_neo4j.py"]),
]

failed = []
for name, files in tests:
    print(f"\n--- {name} ---")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", *files], cwd=ROOT)
    if r.returncode != 0:
        failed.append(name)

if failed:
    print(f"\nFAILED: {failed}")
    sys.exit(1)
print("\n=== All tests passed ===")
