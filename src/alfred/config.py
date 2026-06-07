"""Runtime config for local development and hosted deployments."""

import os

# Railway injects RAILWAY_VOLUME_MOUNT_PATH when a volume is attached. An
# explicit ALFRED_DATA_DIR still wins, which is useful outside Railway.
DATA_DIR = (
    os.environ.get("ALFRED_DATA_DIR")
    or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    or "data/store"
)
