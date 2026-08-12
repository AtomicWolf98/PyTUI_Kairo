"""Kairo Kernel public surface."""

from kairo_kernel import contracts, ports
from kairo_kernel._version import __version__
from kairo_kernel.bootstrap import KernelOpenOptions, OpenedKernel, open_kernel
from kairo_kernel.contracts.lifecycle import KERNEL_API_VERSION
from kairo_kernel.errors import KernelError, KernelResult
from kairo_kernel.factory import KernelConfig, KernelDependencies, build_kernel
from kairo_kernel.kernel import KairoKernel

__all__ = [
    "KairoKernel",
    "KERNEL_API_VERSION",
    "KernelConfig",
    "KernelDependencies",
    "KernelError",
    "KernelOpenOptions",
    "KernelResult",
    "OpenedKernel",
    "__version__",
    "build_kernel",
    "contracts",
    "open_kernel",
    "ports",
]
