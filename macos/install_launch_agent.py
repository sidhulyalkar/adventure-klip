#!/usr/bin/env python3
"""Generate a macOS launchd agent for card mount events. --install required to activate."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

LABEL="dev.adventure-intelligence.card-import"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",default="~/AdventureAI")
    parser.add_argument("--install",action="store_true",help="Actually install and bootstrap agent")
    parser.add_argument("--analyze-on-import",action="store_true",
                        help="Also run lightweight baseline motion analysis after importing; still no cloud uploads")
    args=parser.parse_args()
    if sys.platform!="darwin":
        raise SystemExit("macOS only")
    binary=shutil.which("adventure")
    if not binary:
        raise SystemExit("Install package first: python3 -m pip install -e .")
    workspace=Path(args.workspace).expanduser().resolve()
    logs=Path.home()/"Library"/"Logs"/"adventure-intelligence"
    target=Path.home()/"Library"/"LaunchAgents"/f"{LABEL}.plist"
    plist={"Label":LABEL,"ProgramArguments":[binary,"--workspace",str(workspace),"watch"] + (["--process"] if args.analyze_on_import else []),
           "RunAtLoad":True,"StartOnMount":True,"StartInterval":300,"StandardOutPath":str(logs/"mount.log"),
           "StandardErrorPath":str(logs/"mount-error.log"),"ProcessType":"Background"}
    if not args.install:
        print(plistlib.dumps(plist).decode())
        print("Preview only. Add --install to activate.")
        return
    if target.exists():
        raise SystemExit(f"Already exists, not overwriting: {target}")
    target.parent.mkdir(parents=True,exist_ok=True)
    logs.mkdir(parents=True,exist_ok=True)
    target.write_bytes(plistlib.dumps(plist))
    try:
        subprocess.run(["launchctl","bootstrap",f"gui/{os.getuid()}",str(target)],check=True)
    except subprocess.CalledProcessError:
        target.unlink(missing_ok=True)
        raise
    print(f"Installed {target}. Logs in {logs}. Watch scans only recognized DJI cards. Analysis is opt-in.")

if __name__=="__main__":
    main()
