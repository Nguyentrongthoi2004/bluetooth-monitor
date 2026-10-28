"""Temporary test data with inherited Windows ACLs (also works in the sandbox)."""
from pathlib import Path
import shutil
import uuid


class TemporaryDirectory:
    def __init__(self, dir=None):
        # Python 3.14's mode=0700 tempfile ACL excludes this sandbox's write SID.
        # Only this UUID directory is removed; no pre-existing data is touched.
        self.root = Path(dir or Path(__file__).parent).resolve()
        self.path = self.root / ('test-tmp-' + uuid.uuid4().hex)
        self.path.mkdir(mode=0o777)
        self.name = str(self.path)

    def __enter__(self):
        return self.name

    def __exit__(self, *args):
        self.cleanup()

    def cleanup(self):
        assert self.path.resolve().parent == self.root
        if self.path.exists():
            shutil.rmtree(self.path)
