"""手动稳定性测试：人为让一个邀请码请求卡死 9 秒，验证看门狗能收尾并补回。

用法：python tests/simulate_stall.py（会请求真实接口）
"""
import sys, time
sys.path.insert(0, r'D:\小程序\爱问云')
from token_source import acquire_tokens
from net import authenticate
from config import discover_base_urls
import pipeline

batch = acquire_tokens(scan_timeout=15)
urls = ([batch.base_url] if batch.base_url else []) + discover_base_urls()
session, cand = authenticate(urls, batch.candidates, timeout=20)
rows = session.fetch_timetable()
real = session.fetch_invite_code
stuck_id = rows[5]['id']
state = {'blocked': False}

def flaky(class_id, deadline=None):
    if class_id == stuck_id and not state['blocked']:
        state['blocked'] = True
        time.sleep(9)
        raise TimeoutError('simulated stuck')
    return real(class_id, deadline)

session.fetch_invite_code = flaky

class State:
    def current(self):
        return session, 0
    def refresh(self, gen):
        return session, 0

options = pipeline.ExportOptions(workers=6, rate=20, class_deadline=6,
                                 stall_timeout=4, retry_budget=10)
stats = {}
started = time.monotonic()
results, canceled = pipeline.fetch_invites(State(), rows, options,
                                           on_stats=lambda s: stats.update(s))
elapsed = time.monotonic() - started
ok = sum(1 for r in results if r['code'])
print('elapsed %.2fs ok %d/%d canceled %s' % (elapsed, ok, len(results), canceled))
print('stats', stats)
