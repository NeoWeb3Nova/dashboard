"""Public scanner backoff for an exchange's actual 418/429 response."""
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_blocked={}
_lock=threading.Lock()
BACKOFF_PATH=Path('/home/neo/projects/hummingbot/hummingbot-api/bots/data/funding_public_backoff.json')


class PublicRateLimited(OSError):
    pass


def public_rate_limits(now=None):
    now=time.time() if now is None else now
    with _lock:
        if BACKOFF_PATH is not None:
            try:
                for host,row in json.loads(BACKOFF_PATH.read_text()).items():
                    if isinstance(row,dict) and isinstance(row.get('until'),(int,float)) and row['until']>_blocked.get(host,{}).get('until',0):
                        _blocked[host]={'until':row['until'],'status':int(row['status'])}
            except (OSError,ValueError,TypeError,KeyError):pass
        return {host:dict(row,remaining_seconds=max(0,row['until']-now))
                for host,row in _blocked.items() if row['until']>now}


def public_urlopen(request,timeout=5):
    url=request.full_url if hasattr(request,'full_url') else request
    host=urllib.parse.urlsplit(url).hostname
    now=time.time()
    current=public_rate_limits(now).get(host)
    if current:
        raise PublicRateLimited(f"{host}: HTTP {current['status']} backoff until {current['until']}")
    try:
        return urllib.request.urlopen(request,timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code not in (418,429):raise
        raw=exc.read(8192).decode('utf-8','replace')
        # Error bodies and headers are used only for timing; never store proxy IPs or credentials.
        until=now+(900 if exc.code==418 else 60)
        explicit=[]
        match=re.search(r'banned until\s+(\d{10,})',raw,re.IGNORECASE)
        if match:
            value=int(match.group(1));explicit.append(value/1000 if value>1e11 else value)
        retry=exc.headers.get('Retry-After') if exc.headers else None
        if retry:
            try:explicit.append(now+float(retry))
            except ValueError:
                try:explicit.append(parsedate_to_datetime(retry).timestamp())
                except (TypeError,ValueError,OverflowError):pass
        if explicit:until=max([now+1]+explicit)
        with _lock:
            until=max(until,_blocked.get(host,{}).get('until',0))
            _blocked[host]={'until':until,'status':exc.code}
            if BACKOFF_PATH is not None and BACKOFF_PATH.parent.is_dir():
                temporary=BACKOFF_PATH.with_suffix('.tmp')
                try:
                    temporary.write_text(json.dumps(_blocked));os.replace(temporary,BACKOFF_PATH)
                except OSError:pass
        raise PublicRateLimited(f'{host}: HTTP {exc.code}; public scanner paused until {until}') from None
