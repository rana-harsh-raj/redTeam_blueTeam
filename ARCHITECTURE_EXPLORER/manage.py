#!/usr/bin/env python3
"""No-dependency Explorer setup and portable static export."""
import argparse
import json
from pathlib import Path
import shutil
import sys
root=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('action',choices=['install','build']);a=p.parse_args()
if sys.version_info<(3,10):raise SystemExit('Python 3.10+ is required')
json.loads((root/'data/architecture.json').read_text())
if a.action=='install':print('Ready: Python standard library only; no packages to install.')
else:
    dest=root/'dist';dest.mkdir(exist_ok=True)
    for f in ('index.html','style.css','app.js'):shutil.copyfile(root/f,dest/f)
    shutil.copytree(root/'data',dest/'data',dirs_exist_ok=True)
    print('Static export: '+str(dest)+'\nServe with: python3 -m http.server 18766 --bind 127.0.0.1 --directory '+str(dest))
