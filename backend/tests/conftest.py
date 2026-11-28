"""Shared test set-up."""
import os

# Imports in tests would otherwise start drawing parchment tiles in the background (#57 follow-up): tests that want
# it call atlas.parchment.warm directly.
os.environ.setdefault("PARCHMENT_WARM_ZOOM", "0")
