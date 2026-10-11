"""Fail before a costly build when fresh VM execution is unavailable."""
import fcntl
import os
import shutil

minimumBytes = 20 * 1024**3
if shutil.disk_usage(".").free < minimumBytes:
    raise SystemExit("Runtime validation requires at least 20 GiB of free disk space")
try:
    kvmFd = os.open("/dev/kvm", os.O_RDWR)
    assert fcntl.ioctl(kvmFd, 0xAE00) == 12, "Unsupported KVM API"
    vmFd = fcntl.ioctl(kvmFd, 0xAE01, 0)
    os.close(vmFd)
    os.close(kvmFd)
except (OSError, AssertionError) as error:
    raise SystemExit(f"Fresh VM execution requires writable, usable KVM: {error}")
