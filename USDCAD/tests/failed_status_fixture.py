"""Emit a real fatal-update status and retained snapshot, without source access."""
from pathlib import Path
import json
import hashlib
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.update import ROOT, update


def main():
    original = (ROOT / 'data/dashboard.json').read_bytes()
    with tempfile.TemporaryDirectory(prefix='usdcad-fatal-fixture-') as directory:
        target = Path(directory)
        (target / 'dashboard.json').write_bytes(original)
        # Fail before calculation/source access, inside the real fatal handler.
        with patch('backend.update.read_observations', side_effect=RuntimeError('Synthetic acquisition failure')):
            try:
                update(target, live=False)
            except RuntimeError as exc:
                assert str(exc) == 'Synthetic acquisition failure'
            else:
                raise AssertionError('Expected the backend fatal path')
        assert (target / 'dashboard.json').read_bytes() == original
        status = json.loads((target / 'update-status.json').read_text())
        assert set(status) == {'started_at', 'finished_at', 'result', 'error'}
        print(json.dumps({'snapshot_sha256': hashlib.sha256(original).hexdigest(), 'status': status}))


if __name__ == '__main__':
    main()
