"""BusinessLabs AI Tech Consultant - Unified Multi-Agent Runner.

Launches all three services concurrently with graceful shutdown on Ctrl+C:
- Task 1: Python Knowledge Brain (Port 8000)
- Task 2: Inbox Agent (Port 8001)
- Task 3: Meeting-to-Scope Agent (Port 8002)
"""

import os
import sys
import time
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SERVICES = [
    {
        "name": "Task 1: Python Knowledge Brain",
        "cmd": [sys.executable, "-m", "uvicorn", "task1_knowledge_brain.app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        "url": "http://127.0.0.1:8000",
        "docs": "http://127.0.0.1:8000/docs",
    },
    {
        "name": "Task 2: Inbox Agent",
        "cmd": [sys.executable, "-m", "uvicorn", "task2_inbox_agent.app.main:app", "--host", "127.0.0.1", "--port", "8001"],
        "url": "http://127.0.0.1:8001",
        "docs": "http://127.0.0.1:8001/docs",
    },
    {
        "name": "Task 3: Meeting-to-Scope Agent",
        "cmd": [sys.executable, "-m", "uvicorn", "task3_scope_agent.app.main:app", "--host", "127.0.0.1", "--port", "8002"],
        "url": "http://127.0.0.1:8002",
        "docs": "http://127.0.0.1:8002/docs",
    },
]


def main():
    print("=" * 70)
    print("  BusinessLabs AI Tech Consultant - Unified Monorepo Launcher")
    print("=" * 70)
    print("Starting all 3 autonomous microservices...\n")

    processes = []
    try:
        for s in SERVICES:
            print(f"[*] Launching {s['name']} on {s['url']}...")
            proc = subprocess.Popen(
                s["cmd"],
                cwd=str(BASE_DIR),
                env=os.environ.copy()
            )
            processes.append((s, proc))
            time.sleep(1.0)

        print("\n" + "=" * 70)
        print("  ALL SERVICES RUNNING SUCCESSFULLY")
        print("=" * 70)
        for s in SERVICES:
            print(f"  -> {s['name']:<32} UI: {s['url']:<24} Docs: {s['docs']}")
        print("=" * 70)
        print("Press Ctrl+C to terminate all services safely.\n")

        while True:
            time.sleep(1.0)
            for s, proc in processes:
                if proc.poll() is not None:
                    print(f"[!] Warning: {s['name']} exited with code {proc.returncode}")

    except KeyboardInterrupt:
        print("\n[!] Shutting down all services...")
    finally:
        for s, proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print("[+] All services stopped cleanly.")


if __name__ == "__main__":
    main()
