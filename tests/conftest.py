"""Every test (except the real-Windows shell tests, which need the real per-user folder) gets a
private FileWizard settings folder, so tests never touch your real settings or leak into each other."""

import pytest


@pytest.fixture(autouse=True)
def _private_app_dir(request, tmp_path, monkeypatch):
    if "windows" in request.node.path.parts:
        yield
        return
    # beside (not inside) tmp_path: some tests list their folder
    monkeypatch.setenv("FILEWIZARD_HOME", str(tmp_path.parent / f"{tmp_path.name}-fwhome"))
    from filewizard import util

    util._WRITABLE.clear()  # per-folder writability cache must not leak between tests either
    yield
