import contextlib
import io
import json
import os
import tempfile
import unittest
from decimal import Decimal

from k8s_cost_peek.cli import main
from k8s_cost_peek.cost import DEFAULT_PRICES, aggregate, pod_requests
from k8s_cost_peek.quantity import parse_quantity

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "pods.json")


def pod(containers, ns="default", phase="Running", init=None, overhead=None, labels=None, name="p"):
    spec = {"containers": [{"name": f"c{i}", "resources": {"requests": r} if r else {}}
                           for i, r in enumerate(containers)]}
    if init:
        spec["initContainers"] = init
    if overhead:
        spec["overhead"] = overhead
    return {"metadata": {"name": name, "namespace": ns, "labels": labels or {}},
            "spec": spec, "status": {"phase": phase}}


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


class QuantityTest(unittest.TestCase):
    def test_cpu(self):
        self.assertEqual(parse_quantity("250m"), Decimal("0.25"))
        self.assertEqual(parse_quantity("2"), Decimal(2))
        self.assertEqual(parse_quantity("0.5"), Decimal("0.5"))
        self.assertEqual(parse_quantity(1), Decimal(1))

    def test_memory(self):
        self.assertEqual(parse_quantity("1Gi"), Decimal(2**30))
        self.assertEqual(parse_quantity("128Mi"), Decimal(128 * 2**20))
        self.assertEqual(parse_quantity("1G"), Decimal(10**9))
        self.assertEqual(parse_quantity("129e6"), Decimal(129 * 10**6))
        self.assertEqual(parse_quantity("1.5Gi"), Decimal("1.5") * 2**30)

    def test_rejects_garbage(self):
        for bad in ["", "abc", "1GB", "5mi", "--1"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_quantity(bad)


class PodRequestsTest(unittest.TestCase):
    def test_containers_are_summed(self):
        cpu, mem, any_set = pod_requests(pod([{"cpu": "500m", "memory": "1Gi"}, {"cpu": "100m"}]))
        self.assertEqual((cpu, mem, any_set), (Decimal("0.6"), Decimal(2**30), True))

    def test_init_container_takes_the_max(self):
        p = pod([{"cpu": "250m", "memory": "512Mi"}],
                init=[{"name": "i", "resources": {"requests": {"cpu": "1", "memory": "256Mi"}}}])
        cpu, mem, _ = pod_requests(p)
        self.assertEqual(cpu, Decimal(1))
        self.assertEqual(mem, Decimal(512 * 2**20))

    def test_sidecar_init_container_adds(self):
        p = pod([{"cpu": "1"}], init=[{"name": "s", "restartPolicy": "Always",
                                       "resources": {"requests": {"cpu": "50m"}}}])
        self.assertEqual(pod_requests(p)[0], Decimal("1.05"))

    def test_overhead_is_added(self):
        p = pod([{"cpu": "1", "memory": "1Gi"}], overhead={"cpu": "250m", "memory": "120Mi"})
        cpu, mem, _ = pod_requests(p)
        self.assertEqual(cpu, Decimal("1.25"))
        self.assertEqual(mem, Decimal(2**30 + 120 * 2**20))

    def test_no_requests(self):
        self.assertEqual(pod_requests(pod([None])), (Decimal(0), Decimal(0), False))


class AggregateTest(unittest.TestCase):
    def test_skips_finished_pods_and_flags_unrequested(self):
        groups = aggregate([
            pod([{"cpu": "1"}], ns="a"),
            pod([{"cpu": "4"}], ns="a", phase="Succeeded"),
            pod([{"cpu": "4"}], ns="a", phase="Failed"),
            pod([None], ns="b", name="bare"),
        ])
        self.assertEqual(groups["a"].cpu, Decimal(1))
        self.assertEqual(groups["a"].pods, 1)
        self.assertEqual(groups["b"].unrequested, ["b/bare"])

    def test_group_by_label(self):
        groups = aggregate([pod([{"cpu": "1"}], labels={"team": "x"}), pod([{"cpu": "1"}])], label="team")
        self.assertEqual(set(groups), {"x", "(no label)"})

    def test_monthly_cost(self):
        u = aggregate([pod([{"cpu": "1", "memory": "1Gi"}])])["default"]
        expected = (DEFAULT_PRICES["cpu_hour"] + DEFAULT_PRICES["gib_hour"]) * 730
        self.assertEqual(u.monthly_cost(DEFAULT_PRICES), expected)


class CliTest(unittest.TestCase):
    def test_example_table(self):
        code, out, _ = run(EXAMPLE)
        self.assertEqual(code, 0)
        first = out.splitlines()[1].split()
        self.assertEqual(first[0], "payments")
        self.assertIn("TOTAL", out)
        self.assertIn("503.31", out)
        self.assertIn("search/search-scratch", out)

    def test_json_and_custom_prices(self):
        code, out, _ = run(EXAMPLE, "--format", "json", "--cpu-price", "0", "--mem-price", "0")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["monthly_total_usd"], 0)
        self.assertEqual(len(data["groups"]), 6)

    def test_csv(self):
        code, out, _ = run(EXAMPLE, "--format", "csv", "--sort", "name")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("group,pods,cpu_cores"))
        self.assertTrue(lines[1].startswith("batch,"))

    def test_budget(self):
        self.assertEqual(run(EXAMPLE, "--budget", "1000")[0], 0)
        code, _, err = run(EXAMPLE, "--budget", "300")
        self.assertEqual(code, 1)
        self.assertIn("over the $300.00 budget", err)

    def test_single_pod_and_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            single = os.path.join(tmp, "pod.json")
            with open(single, "w") as f:
                json.dump({"kind": "Pod", **pod([{"cpu": "1"}], ns="solo")}, f)
            self.assertIn("solo", run(single)[1])
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w") as f:
                json.dump({"kind": "Deployment"}, f)
            code, _, err = run(bad)
            self.assertEqual(code, 2)
            self.assertIn("expected a Pod or a List", err)

    def test_bad_quantity_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "pods.json")
            with open(path, "w") as f:
                json.dump({"items": [pod([{"cpu": "lots"}])]}, f)
            code, _, err = run(path)
            self.assertEqual(code, 2)
            self.assertIn("not a Kubernetes quantity", err)


if __name__ == "__main__":
    unittest.main()
