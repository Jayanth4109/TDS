"""Export encrypted diagnostics only. This runner never holds the decryption key."""
from pathlib import Path
import httpx

base = Path('/tmp/ga3-deployment/tds-public-url.txt').read_text().strip()
root = Path('/tmp/ga3-encrypted-support')
root.mkdir(parents=True, exist_ok=True)
client = httpx.Client(base_url=base, timeout=20)
response = client.get('/support/events')
if response.status_code == 200:
    for name in response.json()['files']:
        response = client.get('/support/events/'+name)
        response.raise_for_status()
        (root/name).write_bytes(response.content)
    print('Encrypted diagnostic packets collected:', len(list(root.glob('*.json'))))
else:
    print('Diagnostic endpoint status:', response.status_code)
