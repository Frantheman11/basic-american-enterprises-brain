"""Short, process-local report cache; callers must authorize before using it."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import threading
import time

_values=OrderedDict()
_lock=threading.RLock()


def read(provider, prof, load, seconds):
    key=(provider,hashlib.sha256(json.dumps(prof,sort_keys=True,default=str).encode()).hexdigest())
    # One shared fetch prevents a burst of browser notifications from repeating a
    # full provider history read. Cache timestamps belong to the original report.
    with _lock:
        now=time.monotonic()
        if key in _values and _values[key][0]>now:
            return deepcopy(_values[key][1])
        result=load()
        ttl=seconds if result.get('state')=='connected' else 15
        _values[key]=(time.monotonic()+ttl,deepcopy(result))
        _values.move_to_end(key)
        while len(_values)>64:_values.popitem(last=False)
        return result
