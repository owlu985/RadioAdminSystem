import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Several service modules initialize the application logger while they are imported.
# Give every test process an isolated, writable data root before importing ``app``;
# otherwise collecting tests as a non-production user can try to open the deployed
# log path before a fixture has an opportunity to override the application config.
TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="rams-tests-"))
os.environ["RAMS_DATA_ROOT"] = str(TEST_DATA_ROOT)
atexit.register(shutil.rmtree, TEST_DATA_ROOT, ignore_errors=True)
