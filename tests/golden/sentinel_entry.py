#!/usr/bin/env python3
"""Stands in for src/vv.py during golden replay: a native call that hands off
lands here, says so, and exits with a code no vv command uses."""
import sys
sys.stderr.write("HANDOFF\n")
sys.exit(42)
