# run_tests.py - Run all tests and report

import subprocess
import sys

tests = [
    ("event_buffer", "test_event_buffer.py"),
    ("KPI calculator", "test_kpi_calculator.py"),
    ("streamlit_app", "test_streamlit_imports.py"),
]

failed = []
for name, script in tests:
    print(f"\n--- {name} ---")
    r = subprocess.run([sys.executable, script], capture_output=False)
    if r.returncode != 0:
        failed.append(name)

if failed:
    print(f"\nFAILED: {failed}")
    sys.exit(1)
print("\n=== All tests passed ===")
