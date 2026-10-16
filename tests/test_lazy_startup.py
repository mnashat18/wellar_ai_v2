from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_api_module_import_does_not_load_media_ml_stack():
    root=Path(__file__).resolve().parents[1]
    env=os.environ.copy(); env.update({"WELLAR_ENV":"test","WELLAR_DB_PATH":":memory:","WELLAR_DEV_AUTH_TOKENS_JSON":"{}"})
    code="import sys,app.main; assert not any(x in sys.modules for x in ('cv2','mediapipe','numpy','PIL')); print('lazy')"
    completed=subprocess.run([sys.executable,"-c",code],cwd=root,env=env,capture_output=True,text=True,timeout=10)
    assert completed.returncode==0,completed.stderr
    assert completed.stdout.strip()=="lazy"
