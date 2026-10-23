#!/usr/bin/env python3
"""Audit/repair ALL textual tags in supported music files. Default: dry run.
No filename inference is applied automatically. Ambiguous conversions are reported.
Each write has a full backup and an ffmpeg audio stream checksum verification.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
import chardet
import ftfy
import mutagen
from mutagen.id3 import ID3

EXTENSIONS = {'.mp3','.flac','.m4a','.mp4','.aac','.ogg','.opus','.wma','.wav','.aiff','.aif','.ape','.wv','.mpc','.tta','.dsf','.dff'}

def norm(text):
    return re.sub(r'[^\w]', '', text.casefold())

def repair_text(text, context):
    # Only encoding repair, no typography, HTML or whitespace normalization.
    # Do not apply ftfy's partial/lossy substitutions to CP1251 text.
    # UTF8 repair must round-trip the ENTIRE original value exactly.
    fixed = ftfy.fix_encoding(text, restore_byte_a0=False, replace_lossy_sequences=False)
    if fixed != text:
        for encoding in ('latin1', 'cp1252'):
            try:
                if fixed.encode('utf8').decode(encoding) == text:
                    return fixed, 'utf8_exact_roundtrip'
            except UnicodeError:
                pass
    if text.isascii():
        return text, None
    try:
        raw = text.encode('latin1')
        candidate = raw.decode('cp1251')
    except UnicodeError:
        return text, None
    letters = [c for c in candidate if c.isalpha()]
    cyr = sum('\u0400' <= c <= '\u04ff' for c in letters)
    if cyr < 3:
        return text, None
    # A complete decoded value visible in the original path is independent evidence.
    if len(norm(candidate)) >= 4 and norm(candidate) in norm(context):
        return candidate, 'cp1251_path_confirmed'
    d = chardet.detect(raw)
    if cyr == len(letters) and '1251' in (d.get('encoding') or '').lower() and d.get('confidence',0) >= .90:
        return candidate, 'cp1251_high_confidence'
    return text, 'ambiguous_cp1251:' + candidate

def audio_hash(path):
    if path.suffix.lower() == '.mp3':
        # Hash exact MPEG payload bytes, excluding ID3v2/v1 only.
        # A demuxer's frame recovery can vary when metadata moves.
        start = ID3(path).size
        end = path.stat().st_size
        with path.open('rb') as stream:
            if end >= 128:
                stream.seek(end-128)
                if stream.read(3) == b'TAG': end -= 128
            stream.seek(start)
            digest = hashlib.sha256()
            remaining = end-start
            while remaining:
                chunk = stream.read(min(1048576,remaining))
                if not chunk: raise IOError('Unexpected audio EOF')
                digest.update(chunk); remaining -= len(chunk)
        return digest.hexdigest()
    p = subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a','-c','copy','-f','hash','-hash','sha256','-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
    if p.returncode or not p.stdout.startswith(b'SHA256='):
        raise RuntimeError('Audio checksum failed: '+p.stderr.decode(errors='replace')[-500:])
    return p.stdout.decode().strip()

def process(path, root, backup_root, apply, overrides):
    rel = str(path.relative_to(root))
    result = {'path':rel,'changes':[],'suspects':[]}
    try:
        media = mutagen.File(path)
        if media is None or media.tags is None:
            result['no_tags'] = True
            return result
        tags = media.tags
        def fix(value, field):
            new, reason = repair_text(value,rel)
            override = overrides.get(rel,{}).get(field)
            if override:
                for rule in (override if isinstance(override,list) else [override]):
                    if value == rule['old']:
                        new,reason = rule['new'],'reviewed_override'
                        break
                # Other COMM/TXXX frames can share FrameID but have different
                # languages/descriptions. Never apply a rule to an unrelated value.
            if new != value:
                result['changes'].append({'field':field,'old':value,'new':new,'reason':reason})
            elif reason:
                result['suspects'].append({'field':field,'value':value,'reason':reason})
            if '\ufffd' in value or re.search(r'[А-Яа-яІіЇїЄє][8=:<>HO]$',value):
                result['suspects'].append({'field':field,'value':value,'reason':'possible_lost_character'})
            return new
        if isinstance(tags,ID3):
            frames=list(tags.values())
            for frame in frames:
                # Never decode binary pictures, private data or identifiers.
                for attr in ('text','desc','url'):
                    value=getattr(frame,attr,None)
                    if isinstance(value,str):
                        setattr(frame,attr,fix(value,frame.FrameID+'.'+attr))
                    elif isinstance(value,list) and all(isinstance(v,str) for v in value):
                        setattr(frame,attr,[fix(v,frame.FrameID+'.'+attr) for v in value])
                if hasattr(frame,'encoding') and any(ch['field'].startswith(frame.FrameID+'.') for ch in result['changes']):
                    frame.encoding=3
            if result['changes']:
                # Rebuild keys for frames whose descriptions are part of HashKey.
                tags.clear()
                for frame in frames: tags.add(frame)
        else:
            for key in list(tags.keys()):
                values=tags[key]
                if isinstance(values,str): tags[key]=fix(values,str(key))
                elif isinstance(values,list) and all(isinstance(v,str) for v in values):
                    tags[key]=[fix(v,str(key)) for v in values]
                # ASF wrappers contain Unicode in .value. Preserve type/metadata.
                elif isinstance(values,list):
                    for value in values:
                        if isinstance(getattr(value,'value',None),str):
                            value.value=fix(value.value,str(key))
        if apply and result['changes']:
            before=audio_hash(path)
            backup=backup_root/rel
            backup.parent.mkdir(parents=True,exist_ok=True)
            with backup.open('xb') as out,path.open('rb') as src: shutil.copyfileobj(src,out)
            # QNAP extended DOS/ACL attributes may not be settable by this user.
            # Copy bytes without trying to duplicate security extended attributes.
            import os, stat
            source_stat = path.stat()
            os.chmod(backup, stat.S_IMODE(source_stat.st_mode))
            os.utime(backup, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            try:
                if isinstance(tags,ID3): tags.save(path,v2_version=3)
                else: media.save()
                after=audio_hash(path)
                if before != after: raise ValueError('Audio checksum changed')
                result['audio_verified']=True
                result['backup']=str(backup)
            except Exception:
                shutil.copyfile(backup,path)
                raise
    except Exception as exc:
        result['error']=str(exc)
    return result

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root',type=Path)
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--report',type=Path,required=True)
    ap.add_argument('--backup-root',type=Path)
    ap.add_argument('--overrides',type=Path)
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--only-paths',type=Path,help='JSON list of reviewed relative paths')
    a=ap.parse_args(); root=a.root.resolve()
    assert root.is_dir()
    if a.apply:
        assert a.backup_root and not a.backup_root.exists(),'A fresh external backup directory is required'
        assert root not in a.backup_root.resolve().parents,'Backups must be outside music root'
        a.backup_root.mkdir(parents=True)
    overrides=json.loads(a.overrides.read_text()) if a.overrides else {}
    files=[p for p in root.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix.lower() in EXTENSIONS and not any(s.startswith(('@','.')) for s in p.relative_to(root).parts[:-1])]
    if a.only_paths:
        selected=json.loads(a.only_paths.read_text())
        files=[root / rel for rel in selected]
        assert all(root in p.resolve().parents and p.is_file() for p in files)
    stats={'total':len(files),'processed':0,'changed_files':0,'changed_fields':0,'no_tags':0,'errors':0,'suspect_files':0,'audio_verified':0,'apply':a.apply}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    with a.report.open('w',encoding='utf8') as out, concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for r in pool.map(lambda p:process(p,root,a.backup_root,a.apply,overrides),files):
            stats['processed']+=1
            stats['changed_files']+=bool(r['changes']); stats['changed_fields']+=len(r['changes'])
            stats['errors']+=bool(r.get('error')); stats['no_tags']+=bool(r.get('no_tags'))
            stats['suspect_files']+=bool(r['suspects']); stats['audio_verified']+=bool(r.get('audio_verified'))
            if r['changes'] or r['suspects'] or r.get('error') or r.get('no_tags'):
                out.write(json.dumps(r,ensure_ascii=False)+'\n'); out.flush()
            if stats['processed']%1000==0: print(json.dumps(stats),flush=True)
    a.report.with_suffix('.summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
    print(json.dumps(stats),flush=True)
    raise SystemExit(bool(stats['errors']))
if __name__=='__main__': main()
