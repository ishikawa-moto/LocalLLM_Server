"""A direct HTTPS navigation entry, never an approval proxy or authority."""
from pathlib import Path
from urllib.parse import urlsplit
import json
from site_settings import site_value

def approval_entry(root):
    try:
        value = json.loads((Path(root)/'config'/'slack-web.json').read_bytes())
        if set(value) != {'enabled', 'approval_url'} or value['enabled'] is not True: return {'enabled':False}
        url = value['approval_url']
        if not isinstance(url, str) or len(url) > 512 or any(c.isspace() or ord(c)<32 for c in url): return {'enabled':False}
        parsed = urlsplit(url)
        expected = site_value('client_address', 'localbrain-client', root).lower()
        if (parsed.scheme != 'https' or parsed.hostname != expected or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or parsed.path != '/slack/approvals'
            or '\\' in url or '%' in url or (parsed.port is not None and not 0 < parsed.port <= 65535)):
            return {'enabled':False}
        return {'enabled':True, 'url':url}
    except (OSError, ValueError, TypeError): return {'enabled':False}
