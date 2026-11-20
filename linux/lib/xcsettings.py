"""Xcode build settings as linux/bin/xcodebuild resolves them: layers from the defaults up to the command line, $(inherited), $(VAR) / ${VAR}, xcconfig files."""
import os, re, shlex
from pathlib import Path

REF = re.compile(r'\$(?:\(([A-Za-z_][A-Za-z0-9_]*)(?::[^)]*)?\)|\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]*)?\})')
LINE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?\s*=\s*(.*?)\s*;?\s*$')


def read_xcconfig(path, sdk='iphoneos', seen=None):
    """The settings of an xcconfig file (its #include files first); conditional assignments [sdk=...] only when they match sdk"""
    seen = seen or set()
    path = Path(path).resolve()
    if path in seen or not path.is_file(): return {}
    seen.add(path)
    out = {}
    for raw in path.read_text(errors='replace').splitlines():
        line = raw.split('//', 1)[0] if '//' in raw and not re.search(r'[a-z]+://', raw) else raw
        if not line.strip(): continue
        m = re.match(r'^\s*#include\??\s+"([^"]+)"', line)
        if m:
            out.update(read_xcconfig(path.parent / m[1], sdk, seen)); continue
        m = LINE.match(line)
        if not m: continue
        cond = m[2]
        if cond:
            c = dict(x.split('=', 1) for x in cond[1:-1].split(',') if '=' in x)
            if 'sdk' in c and not re.fullmatch(c['sdk'].replace('*', '.*'), sdk): continue
            if 'config' in c and c['config'] != os.environ.get('NXN_CONFIGURATION', 'Release'): continue
        out[m[1]] = m[3]
    return out


class Settings:
    def __init__(self, layers):
        self.layers = [dict(l) for l in layers]   # lowest first
        self._cache = {}

    def _raw(self, key, below):
        for i in range(below - 1, -1, -1):
            if key in self.layers[i]: return i, self.layers[i][key]
        return -1, None

    def _resolve(self, key, below, depth=0):
        if depth > 40: return ''
        i, v = self._raw(key, below)
        if v is None: return os.environ.get(key, '')
        v = str(v)
        if '$(inherited)' in v or '${inherited}' in v:
            inh = self._resolve(key, i, depth + 1) if i > 0 else ''
            v = v.replace('$(inherited)', inh).replace('${inherited}', inh)
        return REF.sub(lambda m: self._resolve(m[1] or m[2], len(self.layers), depth + 1) if (m[1] or m[2]) != key else '', v)

    def get(self, key, default=''):
        if key not in self._cache: self._cache[key] = self._resolve(key, len(self.layers)).strip()
        return self._cache[key] or default

    def list(self, key):
        v = self.get(key)
        try: return shlex.split(v)
        except ValueError: return v.split()

    def yes(self, key, default=False):
        v = self.get(key)
        return v.upper() in ('YES', 'TRUE', '1') if v else default

    def expand(self, text):
        """$(VAR) / ${VAR} in a string (an Info.plist value) against these settings"""
        return REF.sub(lambda m: self.get(m[1] or m[2]), text)
