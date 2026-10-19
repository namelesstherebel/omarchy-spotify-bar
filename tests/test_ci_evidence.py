import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / "scripts" / "test_evidence.py"
spec = importlib.util.spec_from_file_location("ci_test_evidence", PATH)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)  # __name__ != "__main__", main not run
SHA = "a" * 40
REAL_RUNNER = unittest.TextTestRunner  # kept before main() tests patch it


def run_fixture(*names):
    class T(unittest.TestCase):  # local: never discovered by the app suite
        def test_pass(self): pass
        def test_fail(self): self.fail("x")
        def test_error(self): raise RuntimeError("x")
        @unittest.skip("x")
        def test_skip(self): pass
        @unittest.expectedFailure
        def test_xfail(self): self.fail("x")
        @unittest.expectedFailure
        def test_xpass(self): pass
    suite = unittest.TestSuite(T(n) for n in names)
    return REAL_RUNNER(stream=io.StringIO()).run(suite)


class SummaryTests(unittest.TestCase):
    def check(self, names, status, code, **counts):
        s = ev.summarize(run_fixture(*names))
        self.assertEqual(s["status"], status)
        self.assertEqual(ev.exit_status(s), code)
        for k, v in counts.items():
            self.assertEqual(s[k], v)

    def test_cases(self):
        self.check(["test_pass"], "passed", 0, executed=1, passed=1)
        self.check(["test_fail"], "failed", 1, failed=1)
        self.check(["test_error"], "failed", 1, errors=1)
        self.check(["test_skip"], "failed", 1, skipped=1)
        self.check(["test_xfail"], "failed", 1, expectedFailures=1)
        self.check(["test_xpass"], "failed", 1, unexpectedSuccesses=1)
        self.check([], "failed", 1, executed=0)


def run_cases(cls, *names):
    suite = unittest.TestSuite(cls(n) for n in names)
    return REAL_RUNNER(stream=io.StringIO()).run(suite)


class SubtestSummaryTests(unittest.TestCase):
    def test_failed_subtests_count_one_parent(self):
        class F(unittest.TestCase):  # local: never discovered
            def test_a(self): pass
            def test_b(self): pass
            def test_c(self):
                for i in range(2):
                    with self.subTest(i=i):
                        self.fail("x")
        s = ev.summarize(run_cases(F, "test_a", "test_b", "test_c"))
        self.assertEqual((s["executed"], s["passed"], s["failed"], s["errors"]), (3, 2, 1, 0))
        self.assertEqual(s["status"], "failed")
        self.assertEqual(ev.exit_status(s), 1)

    def test_mixed_failure_and_error_precedence(self):
        class M(unittest.TestCase):
            def test_m(self):
                with self.subTest("f"):
                    self.fail("x")
                with self.subTest("e"):
                    raise RuntimeError("x")
        s = ev.summarize(run_cases(M, "test_m"))
        self.assertEqual((s["executed"], s["passed"], s["failed"], s["errors"]), (1, 0, 0, 1))
        self.assertEqual(ev.exit_status(s), 1)

    def test_skipped_subtest_fails_closed(self):
        class S(unittest.TestCase):
            def test_ok(self): pass
            def test_s(self):
                with self.subTest("s"):
                    self.skipTest("x")
        s = ev.summarize(run_cases(S, "test_ok", "test_s"))
        self.assertEqual(s["executed"], 2)
        self.assertEqual(s["passed"], 1)
        self.assertEqual(s["skipped"], 1)
        self.assertEqual(s["status"], "failed")
        self.assertEqual(ev.exit_status(s), 1)
        total = sum(s[k] for k in ("passed", "failed", "errors", "skipped",
                                   "expectedFailures", "unexpectedSuccesses"))
        self.assertEqual(total, s["executed"])


class SetupHolderTests(unittest.TestCase):
    def check(self, cls):  # local classes only: never discovered
        s = ev.summarize(run_cases(cls, "test_x"))
        self.assertEqual((s["executed"], s["passed"], s["nonTestOutcomes"]), (0, 0, 1))
        self.assertEqual(s["status"], "failed")
        self.assertEqual(ev.exit_status(s), 1)

    def test_setupclass_error(self):
        class E(unittest.TestCase):
            @classmethod
            def setUpClass(cls): raise RuntimeError("x")
            def test_x(self): pass
        self.check(E)

    def test_setupclass_skip(self):
        class K(unittest.TestCase):
            @classmethod
            def setUpClass(cls): raise unittest.SkipTest("x")
            def test_x(self): pass
        self.check(K)


class MainTests(unittest.TestCase):
    def main(self, head, discover=None):
        loader = mock.Mock()
        loader.return_value.discover.side_effect = discover or (lambda *a: unittest.TestSuite())
        runner = mock.Mock()
        runner.return_value.run.side_effect = lambda s: run_fixture("test_pass")
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as out, \
                mock.patch.object(ev, "_head", return_value=head), \
                mock.patch.object(ev.unittest, "TestLoader", loader), \
                mock.patch.object(ev.unittest, "TextTestRunner", runner), \
                mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(Path(d) / "s.md")}):
            code = ev.main()
            text = (Path(d) / "s.md").read_text()
        return code, json.loads(out.getvalue()), text

    def test_valid_pass(self):
        code, rec, text = self.main(SHA)
        self.assertEqual(code, 0)
        self.assertEqual((rec["commit"], rec["status"], rec["passed"]), (SHA, "passed", 1))
        self.assertIn(SHA, text)

    def test_bad_head(self):
        for head in ("unknown", "", "g" * 40, SHA[:39]):
            code, rec, _ = self.main(head)
            self.assertNotEqual(code, 0)
            self.assertEqual(rec["status"], "error")

    def test_discovery_error(self):
        def boom(*a): raise ImportError("x")
        code, rec, _ = self.main(SHA, boom)
        self.assertNotEqual(code, 0)
        self.assertEqual(rec["status"], "error")


if __name__ == "__main__":
    unittest.main()
