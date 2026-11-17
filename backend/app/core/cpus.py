"""How many CPUs this process may really use.

``os.cpu_count()`` reports the host's cores and knows nothing about a container's CPU quota, so inside Docker on a
bigger host it over-counts and every pool sized from it (page readers, Tesseract processes) oversubscribes the CPU it
actually gets: nothing goes faster, pages contend, and each one holds a 300-DPI bitmap in memory. The smallest of the
sources below wins; each is ignored where it does not apply.
"""
import os
from pathlib import Path


def available_cpus() -> int:
    limits = [os.cpu_count() or 4]
    try:  # cgroup v2: "<quota> <period>", or "max <period>" when unlimited
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            limits.append(max(1, int(float(quota) / float(period))))
    except Exception:  # not a cgroup v2 host, or not readable
        pass
    try:  # cgroup v1: quota and period live in separate files, -1 means unlimited
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            limits.append(max(1, quota // period))
    except Exception:
        pass
    try:  # a pinned process (Linux only)
        limits.append(len(os.sched_getaffinity(0)))
    except AttributeError:
        pass
    return max(1, min(limits))


def default_page_workers(cpus: int | None = None) -> int:
    """Pages the local OCR reads at once when OCR_PAGE_WORKERS is not set: half the usable CPUs, at most 4, at least 1.

    Measured on 12 scanned pages (8 cores / 12 logical): 1 worker 21 s, 2 workers 9 s, 4 workers 5.5 s, 8 workers 4.3 s. Past 4 the
    gain is small while every worker holds a page bitmap and a Tesseract process, and the machine also runs the API, the database
    and the other readers; half the CPUs leaves them room (a 2-core host reads one page at a time, as its operators already set)."""
    return max(1, min(4, (cpus if cpus is not None else available_cpus()) // 2))
