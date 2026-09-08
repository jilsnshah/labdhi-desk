#!/usr/bin/env python3
"""Start the desk:  python run.py  ->  http://127.0.0.1:8420"""
import os
import sys

import uvicorn

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    port = int(os.environ.get("PORT", "8420"))
    uvicorn.run("backend.api:app", host="127.0.0.1", port=port,
                reload="--reload" in sys.argv, log_level="info")
