"""Test configuration for headless Qt runs."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
