#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, tarfile
from pathlib import Path

EXCLUDE_PARTS={"__pycache__",".pytest_cache"}

def include(p:Path)->bool:
    return not any(x in EXCLUDE_PARTS for x in p.parts) and p.suffix not in {".pyc",".pyo"}

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output",required=True); args=ap.parse_args()
    root=Path(__file__).resolve().parent
    files=sorted(p for p in root.rglob('*') if p.is_file() and include(p) and p.name!="MANIFEST.sha256")
    manifest=[]
    for p in files:
        rel=p.relative_to(root); manifest.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {rel.as_posix()}")
    m=root/"MANIFEST.sha256"; m.write_text("\n".join(manifest)+"\n",encoding="utf-8")
    try:
        files.append(m)
        out=Path(args.output).resolve(); out.parent.mkdir(parents=True,exist_ok=True)
        with tarfile.open(out,"w:gz",format=tarfile.PAX_FORMAT) as tf:
            for p in sorted(files): tf.add(p,arcname=f"chatgpt-shell-bridge/{p.relative_to(root).as_posix()}",recursive=False)
    finally:
        m.unlink(missing_ok=True)
    return 0
if __name__=='__main__': raise SystemExit(main())
