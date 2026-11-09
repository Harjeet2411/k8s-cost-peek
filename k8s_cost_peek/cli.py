"""Command line: kubectl get pods -A -o json | k8s-cost-peek"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from decimal import Decimal, InvalidOperation

from . import __version__
from .cost import DEFAULT_PRICES, GIB, Usage, aggregate


def load_pods(path: str) -> list[dict]:
    if path == "-":
        data = json.load(sys.stdin)
    else:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    if isinstance(data, dict) and data.get("kind") == "Pod":
        return [data]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    raise ValueError("expected a Pod or a List of pods (kubectl get pods -A -o json)")


def _price(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a number: {text}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("price must not be negative")
    return value


def rows(groups: dict[str, Usage], prices: dict[str, Decimal], sort: str) -> list[dict]:
    out = [
        {
            "group": name,
            "pods": u.pods,
            "cpu_cores": float(round(u.cpu, 3)),
            "memory_gib": float(round(u.memory / GIB, 2)),
            "monthly_usd": float(round(u.monthly_cost(prices), 2)),
            "pods_without_requests": len(u.unrequested),
        }
        for name, u in groups.items()
    ]
    key = {"cost": "monthly_usd", "cpu": "cpu_cores", "memory": "memory_gib", "name": "group"}[sort]
    out.sort(key=lambda r: r[key], reverse=sort != "name")
    return out


def render_table(table: list[dict], heading: str) -> str:
    headers = [heading, "PODS", "CPU", "MEM GiB", "$/MONTH", "NO REQ"]
    body = [
        [r["group"], str(r["pods"]), f"{r['cpu_cores']:.3f}", f"{r['memory_gib']:.2f}",
         f"{r['monthly_usd']:,.2f}", str(r["pods_without_requests"]) if r["pods_without_requests"] else ""]
        for r in table
    ]
    total = sum(r["monthly_usd"] for r in table)
    body.append(["TOTAL", str(sum(r["pods"] for r in table)),
                 f"{sum(r['cpu_cores'] for r in table):.3f}",
                 f"{sum(r['memory_gib'] for r in table):.2f}", f"{total:,.2f}", ""])
    widths = [max(len(row[i]) for row in [headers, *body]) for i in range(len(headers))]
    lines = []
    for n, row in enumerate([headers, *body]):
        if n == len(body):  # rule above the total
            lines.append("  ".join("-" * w for w in widths))
        cells = [row[0].ljust(widths[0])] + [c.rjust(w) for c, w in zip(row[1:], widths[1:])]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="k8s-cost-peek",
        description="Price Kubernetes CPU and memory requests per namespace (or per label).",
    )
    p.add_argument("file", nargs="?", default="-", help="kubectl get pods -A -o json output (default: stdin)")
    p.add_argument("--by-label", metavar="KEY", help="group by this pod label instead of namespace, e.g. team")
    p.add_argument("--cpu-price", type=_price, default=DEFAULT_PRICES["cpu_hour"],
                   help="USD per vCPU-hour (default: %(default)s, Fargate us-east-1)")
    p.add_argument("--mem-price", type=_price, default=DEFAULT_PRICES["gib_hour"],
                   help="USD per GiB-hour (default: %(default)s, Fargate us-east-1)")
    p.add_argument("--sort", choices=["cost", "cpu", "memory", "name"], default="cost")
    p.add_argument("--format", choices=["table", "json", "csv"], default="table")
    p.add_argument("--budget", type=_price, metavar="USD",
                   help="exit 1 if the monthly total is above this, for CI")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    try:
        pods = load_pods(args.file)
        groups = aggregate(pods, args.by_label)
    except (OSError, ValueError) as exc:
        print(f"k8s-cost-peek: {exc}", file=sys.stderr)
        return 2

    prices = {"cpu_hour": args.cpu_price, "gib_hour": args.mem_price}
    table = rows(groups, prices, args.sort)
    total = round(sum(Decimal(str(r["monthly_usd"])) for r in table), 2)

    if args.format == "json":
        print(json.dumps({"groups": table, "monthly_total_usd": float(total),
                          "prices": {k: float(v) for k, v in prices.items()}}, indent=2))
    elif args.format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(table[0]) if table else ["group"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(table)
        print(buf.getvalue(), end="")
    else:
        print(render_table(table, (args.by_label or "namespace").upper()))
        missing = [name for u in groups.values() for name in u.unrequested]
        if missing:
            print(f"\n{len(missing)} pod(s) set no CPU or memory request, so they cost $0 here "
                  f"but still use the node: {', '.join(sorted(missing)[:5])}"
                  + (" ..." if len(missing) > 5 else ""))

    if args.budget is not None and total > args.budget:
        print(f"k8s-cost-peek: monthly total ${total:,.2f} is over the ${args.budget:,.2f} budget",
              file=sys.stderr)
        return 1
    return 0
