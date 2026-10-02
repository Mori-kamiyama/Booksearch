"""Run interactively to save the tunnel runtime key without echoing it."""
import getpass
import os
from pathlib import Path

path = Path.home() / '.config' / 'booksearch' / 'tunnel-runtime.key'
key = getpass.getpass('Tunnel runtime API key (hidden): ').strip()
if not key.startswith('sk-') or any(c.isspace() for c in key):
    raise SystemExit('Invalid key format; nothing saved.')
path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write(key)
print('Saved locally. The key was not printed.')
