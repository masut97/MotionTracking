"""Checksum paths work in Windows PowerShell even with a PS7 module path."""
import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("source", ["installer", "shortcut"])
def test_dotnet_checksum_has_no_module_dependency(tmp_path, source):
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        pytest.skip("Windows PowerShell required")
    if source == "installer":
        script = (ROOT / "packaging/Installieren.ps1").read_text(encoding="utf-8")
    else:
        from atracsys_sync.shortcuts import SCRIPT
        script = SCRIPT
    helper = re.search(r"function Get-Sha256.*?\n}\n", script, re.S).group()
    content = b"module-independent checksum\x00\xff"
    payload = tmp_path / "file with spaces.bin"
    payload.write_bytes(content)
    check = tmp_path / "check.ps1"
    check.write_text("param([string]$InputPath)\n" + helper + "Get-Sha256 $InputPath\n", encoding="utf-8-sig")
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(check), str(payload)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == hashlib.sha256(content).hexdigest()
