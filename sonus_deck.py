#!/usr/bin/env python3
"""Launcher. The code lives in the sonusdeck package."""

from __future__ import annotations

import sys

from sonusdeck.app import main

if __name__ == "__main__":
    sys.exit(main())
