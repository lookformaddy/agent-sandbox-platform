"""Fail-closed policy engine.

Every check defaults to DENY. A tool call is allowed only when *all*
applicable checks pass:

- network allow-list (deny by default; ``none`` mode denies everything)
- filesystem path scoping (no escape from the sandbox roots)
- secret / credential blocking (argument values are scanned)
- shell command denylist (destructive / exfiltration commands)

High-risk tools additionally require an approval (see approvals.py); the
policy engine reports ``REQUIRE_APPROVAL`` for those and the executor
enforces the gate.
"""
from __future__ import annotations

import posixpath
import re
from urllib.parse import urlparse

from sandbox.models import (
    Decision, NetworkMode, PolicyDecision, RiskTier, SandboxConfig, ToolCall,
    ToolDefinition,
)

# Patterns that look like credentials. Anything matching in *any* string
# argument value blocks the call — secrets must never enter a sandbox.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),                       # AWS access key id
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}"),                   # API key style
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),                 # GitHub PAT
    re.compile(r"\bgho_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),         # Slack token
    re.compile(r"(?i)(api[_-]?key|password|passwd|secret|token)\s*[:=]\s*\S{4,}"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
)

# Shell fragments that are never allowed, however they are spelled.
COMMAND_DENYLIST: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+(-[a-z]*r[a-z]*\s+)?/"),           # rm -rf /
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b.*\bof=/dev/"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bchown\b"),
    re.compile(r"\bcurl\b.*\|\s*(sh|bash)\b"),             # curl | sh
    re.compile(r"\bwget\b.*\|\s*(sh|bash)\b"),
    re.compile(r"\bnc\s+-l\b"),                            # netcat listener
    re.compile(r"\b(ncat|netcat)\b"),
    re.compile(r">\s*/dev/(?:sda|mem|kmem)"),
    re.compile(r"\bshutdown\b|\breboot\b"),
    re.compile(r"\bhistory\s+-c\b"),
    re.compile(r"\beval\s*\("),
    re.compile(r"\b(base64\s+-d|xxd\s+-r)\b.*\|\s*(sh|bash)\b"),
)

# Hosts that are never reachable even in OPEN mode (metadata / internal).
BLOCKED_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal", "localhost", "127.0.0.1"})


def _string_values(value: object) -> list[str]:
    """Collect every string leaf of a nested args structure."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_string_values(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_string_values(v))
    return out


def _normalize_path(path: str) -> str:
    """Lexically normalize a POSIX path, resolving ``.`` and ``..``."""
    return posixpath.normpath(path)


def path_in_scope(path: str, roots: tuple[str, ...]) -> bool:
    """True when the normalized path sits inside one of the roots."""
    normalized = _normalize_path(path)
    for root in roots:
        root_norm = _normalize_path(root)
        if normalized == root_norm or normalized.startswith(root_norm.rstrip("/") + "/"):
            return True
    return False


def host_allowed(host: str, mode: NetworkMode,
                 allowlist: tuple[str, ...]) -> bool:
    """Network decision for one host under the sandbox's network mode."""
    host = (host or "").lower().strip()
    if not host or host in BLOCKED_HOSTS:
        return False
    if mode is NetworkMode.NONE:
        return False
    if mode is NetworkMode.OPEN:
        return True
    return host in {h.lower() for h in allowlist}


class PolicyEngine:
    """Fail-closed policy checks for tool calls."""

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config

    # -- individual checks -------------------------------------------------
    def check_secrets(self, call: ToolCall) -> list[str]:
        reasons: list[str] = []
        for value in _string_values(call.args):
            for pattern in SECRET_PATTERNS:
                if pattern.search(value):
                    reasons.append(
                        f"secret/credential pattern detected in argument "
                        f"value for tool {call.tool!r}")
                    break
        return reasons

    def check_network(self, call: ToolCall, tool: ToolDefinition,
                      config: SandboxConfig) -> list[str]:
        reasons: list[str] = []
        if tool.behaviour.get("kind") != "http":
            return reasons
        url = str(call.args.get("url", ""))
        host = urlparse(url).hostname or ""
        if not host_allowed(host, config.network_mode, config.network_allowlist):
            reasons.append(
                f"network host {host or url!r} is not allow-listed "
                f"(mode={config.network_mode.value})")
        return reasons

    def check_filesystem(self, call: ToolCall, tool: ToolDefinition,
                         config: SandboxConfig) -> list[str]:
        reasons: list[str] = []
        if tool.behaviour.get("kind") not in ("file_read", "file_write"):
            return reasons
        path = str(call.args.get("path", ""))
        if not path:
            reasons.append("missing required 'path' argument")
        elif not path_in_scope(path, config.fs_roots):
            reasons.append(
                f"filesystem path {path!r} escapes sandbox roots "
                f"{list(config.fs_roots)}")
        return reasons

    def check_command(self, call: ToolCall, tool: ToolDefinition) -> list[str]:
        reasons: list[str] = []
        if tool.behaviour.get("kind") != "shell":
            return reasons
        command = str(call.args.get("command", ""))
        for pattern in COMMAND_DENYLIST:
            if pattern.search(command):
                reasons.append(
                    f"denylisted shell command fragment in {command!r}")
                break
        return reasons

    # -- combined decision -------------------------------------------------
    def decide(self, call: ToolCall, tool: ToolDefinition,
               config: SandboxConfig) -> PolicyDecision:
        reasons: list[str] = []
        reasons.extend(self.check_secrets(call))
        reasons.extend(self.check_network(call, tool, config))
        reasons.extend(self.check_filesystem(call, tool, config))
        reasons.extend(self.check_command(call, tool))
        if reasons:
            return PolicyDecision(decision=Decision.DENY, reasons=reasons)
        if tool.risk_tier is RiskTier.HIGH:
            return PolicyDecision(
                decision=Decision.REQUIRE_APPROVAL,
                reasons=[f"tool {tool.name!r} is high-risk and requires approval"])
        return PolicyDecision(decision=Decision.ALLOW)
