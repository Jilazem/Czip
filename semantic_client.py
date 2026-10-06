"""Optional loopback embedding retrieval. This client has no ML dependencies."""
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request


def search(query, limit=20, kind=None, case=None, config_path=None):
    default = Path(os.environ.get('CZIP_HERMES_ROOT', os.environ.get('HERMES_HOME', '~/.hermes'))).expanduser()/'runtime/czip-is-kaynagi/semantic.json'
    path = Path(config_path or os.environ.get('CZIP_SEMANTIC_CONFIG', default))
    if not path.exists():
        return {'status': 'disabled', 'hits': []}
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        if config.get('enabled') is not True:
            return {'status': 'disabled', 'hits': []}
        url = config['url'].rstrip('/')
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError('Embedding service must be a plain loopback HTTP origin')
        token = Path(config['token_file']).read_text(encoding='utf-8').strip()
        payload = json.dumps({'query': query, 'limit': max(1, min(int(limit), 40)), 'kind': kind, 'case': case}).encode()
        request = urllib.request.Request(url+'/search', payload,
            {'Content-Type': 'application/json', 'Authorization': 'Bearer '+token})
        # Ignore proxy environment: source queries must never leave loopback.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=max(.1, min(float(config.get('timeout', 3)), 5))) as response:
            data = json.loads(response.read(131072))
        if not isinstance(data.get('hits'), list):
            raise ValueError('Invalid semantic response')
        return data
    except Exception as error:
        # Never expose token-bearing URLs, query text or exception bodies.
        return {'status': 'unavailable', 'hits': [], 'reason': type(error).__name__}
