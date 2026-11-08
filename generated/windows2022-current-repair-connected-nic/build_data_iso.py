from pathlib import Path
import io
import pycdlib

root = Path(__file__).parent
script = (root / 'prepare-data-disk.ps1').read_bytes()
cmd = b'@echo off\r\n%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~d0\\ERASE.PS1"\r\npause\r\n'
iso = pycdlib.PyCdlib()
iso.new(interchange_level=3, joliet=3, vol_ident='DATADISK2')
iso.add_fp(io.BytesIO(script), len(script), iso_path='/ERASE.PS1;1', joliet_path='/ERASE.PS1')
iso.add_fp(io.BytesIO(cmd), len(cmd), iso_path='/RUNME.CMD;1', joliet_path='/RUNME.CMD')
iso.write(str(root / 'data-disk-v2.iso'))
iso.close()
