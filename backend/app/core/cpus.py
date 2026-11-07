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
