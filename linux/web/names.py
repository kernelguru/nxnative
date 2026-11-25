"""Game folder names without spaces.

Apple's Windows Metal compiler, run under Wine, splits a path argument at its spaces (seen with "Mario Kart World": every shader failed with "no such file or directory:
'Z:\\data\\games\\Mario'", 'Kart', 'World\\...'), and every path of a build lies in its game folder. So a folder gets hyphens for spaces wherever it is named (a RomM import, an
upload, a new folder), and the ones made before, or by hand, are renamed at the start (fix_folders) and before a build (fix_folder).
"""
import re
from pathlib import Path


def folder_name(name):
    """'Mario Kart World' → 'Mario-Kart-World', 'Pokémon Legends: Arceus' → 'Pokémon-Legends-Arceus': white space, and characters a folder name of the web interface
    does not take (: / ? * " < > | and the like, which RomM's names have), become one hyphen; no hyphen run, none at the ends"""
    n = re.sub(r"[^\w.,()\[\]&+'!-]+", '-', (name or '').strip())
    return re.sub(r'-{2,}', '-', n).strip('-')


def fix_folder(path):
    """path renamed to its name without spaces, unless it has none or that name is taken; returns (the folder now, a note or None)"""
    path = Path(path)
    new = folder_name(path.name)
    if new == path.name: return path, None
    dst = path.with_name(new)
    if dst.exists(): return path, f'{path.name!r} keeps its spaces: {new!r} exists (move its files there, or rename one of them)'
    path.rename(dst)
    return dst, f'{path.name!r} renamed {new!r} (spaces break the Metal compiler under Wine)'


def fix_folders(root):
    """Every folder under root that has white space in its name, renamed; the notes"""
    notes = []
    for d in sorted(Path(root).iterdir()):
        if d.is_dir() and re.search(r'\s', d.name):
            _, note = fix_folder(d)
            if note: notes.append(note)
    return notes
