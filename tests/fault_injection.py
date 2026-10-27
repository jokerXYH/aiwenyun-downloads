"""手动稳定性测试：45% 请求随机网络失败 + 10% token 过期。

用法：python tests/fault_injection.py（会请求真实接口）
"""
import sys, time, random
sys.path.insert(0, r'D:\小程序\爱问云')
from token_source import acquire_tokens
from net import authenticate, NetworkError, TokenExpired
from config import discover_base_urls
import pipeline

batch = acquire_tokens(scan_timeout=15)
urls = ([batch.base_url] if batch.base_url else []) + discover_base_urls()
session, cand = authenticate(urls, batch.candidates, timeout=20)
rows = session.fetch_timetable()
real = session.fetch_invite_code
random.seed(7)

def flaky(class_id, deadline=None):
    r = random.random()
    if r < 0.35:
        raise NetworkError('simulated network failure')
    if r < 0.45:
        raise TokenExpired('simulated token expired')
    time.sleep(random.uniform(0.02, 0.2))
    return real(class_id, deadline)

session.fetch_invite_code = flaky

class State:
    def current(self):
        return session, 0
    def refresh(self, gen):
        return session, 0

options = pipeline.ExportOptions(workers=8, rate=12, retry_budget=20)
stats = {}
started = time.monotonic()
results, canceled = pipeline.fetch_invites(State(), rows, options,
                                           on_stats=lambda s: stats.update(s))
elapsed = time.monotonic() - started
ok = sum(1 for r in results if r['code'])
print('faulty network: elapsed %.2fs ok %d/%d' % (elapsed, ok, len(results)))
print('retry', stats['retry'], 'attempts', stats['attempts'], 'failures', len(stats['failures']))
