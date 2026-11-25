"""bubblewrap 沙箱（M8 第 6b 项，提案第 4 步）。

本模块只做两件事：**探测沙箱是否可用**，以及**构造沙箱命令**。
它本身不启动进程（那是 process.py 的职责），因此可以单独测试。

设计原则（全部来自 §8.5 与提案 §4）：

1. **bwrap 不可用 → 拒绝运行**，而不是无沙箱照常跑。
   沙箱缺失时降级为放行，等于沙箱只是个摆设。
2. **默认拒绝所有访问**，只显式挂载插件需要的路径。
3. **只读根文件系统**：插件目录以只读方式挂到 `/plugin`，
   插件无法改写自己的包（否则可持久化恶意代码）。
4. **无网络**：`--unshare-net`（本机实测可阻断出站连接）。
5. **不继承环境变量**：只传最少几个，宿主进程里的模型 Key 不会被带过去。
6. **工作目录**：一个可写的 tmpfs，插件只能写这里，进程结束即消失。

关于可写空间：插件往往需要写临时文件，但又不应该写进用户的数据目录。
因此给 `/work` 一个 tmpfs（大小受 limits 影响），进程退出后内容消失。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.plugins.manifest import PluginLimits

logger = logging.getLogger(__name__)

BWRAP_BINARY = "bwrap"
PRLIMIT_BINARY = "prlimit"

# 沙箱里插件的挂载点。固定路径让插件作者有稳定的预期。
PLUGIN_MOUNT = "/plugin"
# 沙箱内的工具目录（目前只有 prlimit）。独立于插件目录，避免挂载冲突。
SANDBOX_BIN = "/sandbox-bin"
WORK_MOUNT = "/work"

# 需要以只读方式提供的系统目录。缺哪个就跳过哪个（不同发行版布局不同）。
# 注意：/bin /sbin /lib /lib64 /lib32 在多数发行版里是 /usr 下目录的符号链接，
# 它们由 _ROOT_SYMLINKS 重建，**不能**再出现在绑定列表里——
# 同一个挂载点既 bind 又 symlink 会冲突（实测：/bin/true 找不到）。
_SYSTEM_RO_BINDS = (
    "/usr",
    "/etc/alternatives",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/localtime",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/group",
    "/etc/resolv.conf",
)

# 沙箱内环境变量白名单。刻意不含 PATH 之外的任何宿主变量。
_SANDBOX_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TERM")

DEFAULT_SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin"


class SandboxUnavailable(RuntimeError):
    """沙箱不可用。调用方必须据此拒绝运行进程插件，绝不能降级放行。"""


@dataclass(frozen=True, slots=True)
class SandboxCommand:
    """一条可直接执行的沙箱命令（argv 列表，不经 shell）。"""

    argv: tuple[str, ...]
    env: dict[str, str]

    def __str__(self) -> str:
        # 仅用于日志与诊断；真实执行用 argv，绝不经过 shell 解释。
        return " ".join(self.argv)


def detect_sandbox() -> tuple[bool, str]:
    """探测 bwrap 与 prlimit 是否可用。

    返回 (可用, 原因)。不可用时给出**可操作**的说明。
    """
    bwrap = shutil.which(BWRAP_BINARY)
    if bwrap is None:
        return False, (
            "未找到 bubblewrap（bwrap）。进程插件需要它来隔离运行环境。"
            "在 Debian/Ubuntu 上可安装：sudo apt install bubblewrap"
        )

    prlimit = shutil.which(PRLIMIT_BINARY)
    if prlimit is None:
        return False, (
            "未找到 prlimit（属于 util-linux）。进程插件需要它来限制资源使用。"
            "在 Debian/Ubuntu 上可安装：sudo apt install util-linux"
        )

    # 功能测试：真实跑一次最小沙箱，而不是只看文件存在。
    # 某些环境（容器/受限内核）里 bwrap 存在但用户命名空间被禁用。
    import subprocess

    try:
        result = subprocess.run(
            [
                bwrap,
                *_root_binds(),
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--unshare-all",
                "--unshare-net",
                "--die-with-parent",
                "--new-session",
                "/bin/true",
            ],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"bubblewrap 无法运行：{type(exc).__name__}"

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        hint = detail[-1] if detail else f"退出码 {result.returncode}"
        return False, f"bubblewrap 功能测试失败：{hint}"

    return True, ""


# 根目录下的兼容性符号链接。很多发行版里 /bin -> usr/bin 是符号链接，
# 在 bwrap 给的空根目录里必须显式重建，否则 /bin/sh 之类全部找不到
# （实测踩过）。
# 这些路径在多数发行版里是 /usr 下目录的符号链接（mergerd-usr 布局）。
# 重建规则**按宿主的实际情况决定**，不要写死：
#   - 宿主上是符号链接 -> 用 --symlink 重建同样的链接
#   - 宿主上是真实目录   -> 用 --ro-bind 只读挂载
#
# 实测教训：若把 /lib（真实目录的方案里）错误地建成指向 usr/lib 的链接，
# 或反过来漏建链接，动态链接器都找不到库，表现为 "execvp: No such file
# or directory"——错误信息看起来像"程序不存在"，实际是库加载失败。
_COMPAT_PATHS = ("/bin", "/sbin", "/lib", "/lib64", "/lib32")


def _compat_mounts() -> list[str]:
    """按宿主文件系统的真实布局重建兼容路径。"""
    args: list[str] = []
    for path in _COMPAT_PATHS:
        target = Path(path)
        if not target.exists():
            continue
        if target.is_symlink():
            link = str(target.readlink())
            args += ["--symlink", link, path]
        else:
            args += ["--ro-bind", path, path]
    return args


def _root_binds() -> list[str]:
    """构造根文件系统的只读挂载参数。"""
    args: list[str] = []
    for path in _existing(_SYSTEM_RO_BINDS):
        args += ["--ro-bind", path, path]
    args += _compat_mounts()
    return args


def _existing(paths: tuple[str, ...]) -> list[str]:
    return [item for item in paths if Path(item).exists()]


def build_command(
    *,
    plugin_dir: Path,
    entrypoint: str,
    arguments: dict[str, object],
    limits: PluginLimits,
) -> SandboxCommand:
    """构造在沙箱中运行插件入口的命令。

    参数通过**标准输入**以 JSON 传入，而不是命令行参数：
    命令行会被同一台机器上的其他用户通过 `ps` 看到，也可能长度有限制。
    """
    import json

    bwrap = shutil.which(BWRAP_BINARY)
    if bwrap is None or shutil.which(PRLIMIT_BINARY) is None:
        raise SandboxUnavailable("沙箱组件不可用。")

    plugin_dir = plugin_dir.resolve()
    if not plugin_dir.is_dir():
        raise SandboxUnavailable("插件目录不存在。")

    entry = (plugin_dir / entrypoint).resolve()
    # 双重检查：规范化之后仍必须在插件目录内。
    if not entry.is_relative_to(plugin_dir):
        raise SandboxUnavailable("插件入口超出了插件目录。")
    if not entry.is_file():
        raise SandboxUnavailable(f"找不到插件入口：{entrypoint}")

    # prlimit 必须放在 **沙箱内部**。实测：放在 bwrap 外面时 RLIMIT_NPROC
    # 按**宿主用户**统计已有进程数（本机已有上千个），任何较小的值都会
    # 让 bwrap 无法创建命名空间（"Resource temporarily unavailable"）。
    # 放进沙箱内，用户命名空间把计数重置为新用户的进程数，限制才真正生效
    # （实测 --nproc=32 时插件 fork 得到 "Cannot fork"）。
    prlimit = shutil.which(PRLIMIT_BINARY)
    if prlimit is None:
        raise SandboxUnavailable("沙箱组件不可用。")

    argv: list[str] = [
        bwrap,
        # ---- 命名空间隔离 ----
        "--unshare-all",  # user/pid/ipc/uts/cgroup 全部隔离
        "--unshare-net",  # 无网络（实测可阻断出站连接）
        "--die-with-parent",  # 宿主退出时子进程一并结束，不留孤儿
        "--new-session",  # 独立会话，不受宿主终端信号影响
        # ---- 文件系统 ----
        # 不用 `--tmpfs /`：实测在 tmpfs 根上 bwrap 无法 execvp 程序
        # （"No such file or directory"）。bwrap 默认给的就是空根目录，
        # 因此直接逐个显式挂载；没挂的东西插件看不见。
        *_root_binds(),
        "--proc",
        "/proc",
        "--dev",
        "/dev",
    ]

    # 系统目录只读挂载。注意 /usr 必须先挂，/bin 往往是它的符号链接。
    for path in _existing(_SYSTEM_RO_BINDS):
        argv += ["--ro-bind", path, path]

    # 插件目录只读（防止插件改写自己的包）
    argv += ["--ro-bind", str(plugin_dir), PLUGIN_MOUNT]

    # prlimit 需要在沙箱内可见（见上面的说明）。单独挂到 /sandbox-bin，
    # 不塞进插件目录（那里是只读的，bwrap 无法在其下创建挂载点）。
    prlimit_path = shutil.which(PRLIMIT_BINARY)
    if prlimit_path is None:
        raise SandboxUnavailable("沙箱组件不可用。")
    argv += ["--ro-bind", prlimit_path, SANDBOX_BIN + "/prlimit"]

    # 可写工作目录：tmpfs，进程结束后内容消失，不会污染用户数据目录
    argv += ["--tmpfs", WORK_MOUNT, "--chdir", WORK_MOUNT]

    # ---- 沙箱内执行 ----
    # prlimit 与解释器都在沙箱内；解释器由调用方追加（见 process.py）。
    argv += [
        SANDBOX_BIN + "/prlimit",
        f"--cpu={limits.cpu_seconds}",
        f"--nproc={limits.max_processes}",
        # RLIMIT_AS 只约束地址空间；对解释器来说足够拦住"申请 2GB"这类行为。
        f"--as={limits.memory_mb * 1024 * 1024}",
        # 禁止生成 core 文件（可能含内存里的敏感数据）
        "--core=0",
        "--",
    ]

    env = {key: _environment(key) for key in _SANDBOX_ENV_KEYS if _environment(key)}
    env.setdefault("PATH", DEFAULT_SANDBOX_PATH)
    env["PLUGIN_ARGS"] = json.dumps(arguments, ensure_ascii=False)
    env["PLUGIN_DIR"] = PLUGIN_MOUNT
    env["PLUGIN_WORKDIR"] = WORK_MOUNT

    return SandboxCommand(argv=tuple(argv), env=env)


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _environment(key: str) -> str:
    import os

    return os.environ.get(key, "")


def describe_unavailable() -> str:
    """给界面用的统一说明。"""
    available, reason = detect_sandbox()
    return "" if available else reason
