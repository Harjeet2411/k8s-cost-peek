"""Turn a pod list into per-group CPU/memory requests and a monthly price."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .quantity import parse_quantity

HOURS_PER_MONTH = Decimal(730)
GIB = Decimal(2**30)

# AWS Fargate on-demand list prices, Linux/x86, us-east-1.
DEFAULT_PRICES = {"cpu_hour": Decimal("0.04048"), "gib_hour": Decimal("0.004445")}

ACTIVE_PHASES = {"Pending", "Running", "Unknown", None}


@dataclass
class Usage:
    cpu: Decimal = Decimal(0)  # cores
    memory: Decimal = Decimal(0)  # bytes
    pods: int = 0
    unrequested: list[str] = field(default_factory=list)

    def monthly_cost(self, prices: dict[str, Decimal]) -> Decimal:
        hourly = self.cpu * prices["cpu_hour"] + (self.memory / GIB) * prices["gib_hour"]
        return hourly * HOURS_PER_MONTH


def _requests(container: dict) -> tuple[Decimal, Decimal, bool]:
    req = (container.get("resources") or {}).get("requests") or {}
    cpu = parse_quantity(req["cpu"]) if "cpu" in req else Decimal(0)
    mem = parse_quantity(req["memory"]) if "memory" in req else Decimal(0)
    return cpu, mem, "cpu" in req or "memory" in req


def pod_requests(pod: dict) -> tuple[Decimal, Decimal, bool]:
    """Effective pod request, the way the scheduler counts it.

    Regular containers are summed; an init container runs alone, so the pod
    needs the larger of that sum and the biggest init container. Pod overhead
    (RuntimeClass) is added on top. The flag says whether any container set a
    request at all.
    """
    spec = pod.get("spec") or {}
    cpu = mem = Decimal(0)
    any_set = False
    for c in spec.get("containers") or []:
        c_cpu, c_mem, c_set = _requests(c)
        cpu, mem, any_set = cpu + c_cpu, mem + c_mem, any_set or c_set
    for c in spec.get("initContainers") or []:
        # Sidecars (restartPolicy: Always) keep running, so they add to the total.
        c_cpu, c_mem, c_set = _requests(c)
        any_set = any_set or c_set
        if c.get("restartPolicy") == "Always":
            cpu, mem = cpu + c_cpu, mem + c_mem
        else:
            cpu, mem = max(cpu, c_cpu), max(mem, c_mem)
    overhead = spec.get("overhead") or {}
    if "cpu" in overhead:
        cpu += parse_quantity(overhead["cpu"])
    if "memory" in overhead:
        mem += parse_quantity(overhead["memory"])
    return cpu, mem, any_set


def group_key(pod: dict, label: str | None) -> str:
    meta = pod.get("metadata") or {}
    if label:
        return (meta.get("labels") or {}).get(label, "(no label)")
    return meta.get("namespace", "default")


def aggregate(pods: list[dict], label: str | None = None) -> dict[str, Usage]:
    groups: dict[str, Usage] = {}
    for pod in pods:
        if (pod.get("status") or {}).get("phase") not in ACTIVE_PHASES:
            continue  # Succeeded/Failed pods hold no resources
        cpu, mem, any_set = pod_requests(pod)
        usage = groups.setdefault(group_key(pod, label), Usage())
        usage.cpu += cpu
        usage.memory += mem
        usage.pods += 1
        if not any_set:
            meta = pod.get("metadata") or {}
            usage.unrequested.append(f"{meta.get('namespace', 'default')}/{meta.get('name', '?')}")
    return groups
