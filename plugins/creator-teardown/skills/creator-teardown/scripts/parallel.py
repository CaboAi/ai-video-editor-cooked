"""Measure several videos at once. Stdlib only.

    python3 parallel.py demo     self-check
"""
import os
import sys


def jobs(n_items, cores=None, env=None):
    """How many videos to measure at once: CT_JOBS, else a quarter of the cores (each worker is itself
    multi-threaded), 1 to 4. ponytail: a core count, not a memory check; CT_JOBS=1 on a small laptop."""
    env = os.environ if env is None else env
    n = int(env.get("CT_JOBS") or 0) or min(4, max(1, (cores or os.cpu_count() or 2) // 4))
    return max(1, min(n, n_items))


def pmap(fn, items, threads=False, tick=None):
    """fn over items, several at once, results in order. fn must be a top-level function (processes).
    threads=True for work that is a subprocess already (whisper, yt-dlp). tick(k) after the k-th one
    finishes, in this process (the page's "video 3 of 7", _progress.ticker)."""
    items = list(items)
    n = jobs(len(items))
    tick = tick or (lambda k: None)
    if n == 1:
        out = []
        for k, x in enumerate(items, 1):
            out.append(fn(x))
            tick(k)
        return out
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
    with (ThreadPoolExecutor if threads else ProcessPoolExecutor)(n) as ex:
        fs = [ex.submit(fn, x) for x in items]
        for k, _ in enumerate(as_completed(fs), 1):
            tick(k)
        return [f.result() for f in fs]


def demo():
    assert jobs(5, cores=14, env={}) == 3 and jobs(5, cores=8, env={}) == 2 and jobs(5, cores=2, env={}) == 1
    assert jobs(2, cores=64, env={}) == 2 and jobs(5, cores=14, env={"CT_JOBS": "1"}) == 1
    seen = []
    assert pmap(abs, [-1, -2, 3], threads=True, tick=seen.append) == [1, 2, 3] and seen == [1, 2, 3]
    print("parallel demo ok")


if __name__ == "__main__":
    sys.exit(demo() if sys.argv[1:] == ["demo"] else print(__doc__))
