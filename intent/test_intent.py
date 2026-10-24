#!/usr/bin/env python3
"""Tests of the intent pipeline (stdlib unittest):  python3 -m unittest -v test_intent.py"""
import copy, json, pathlib, sys, unittest

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import compiler, pipeline, translator, validator   # noqa: E402

CTX = json.loads((HERE / "context.json").read_text(encoding="utf-8"))
REF = json.loads((HERE / "drafts" / "I-tool" / "round2.json").read_text(encoding="utf-8"))
INTENTS = {i["id"]: i for i in json.loads((HERE / "intents.json").read_text(encoding="utf-8"))}


def failed(res):
    return {f["check"] for f in res["findings"] if not f["ok"]}


class ValidatorTests(unittest.TestCase):
    def test_reference_is_accepted(self):
        self.assertEqual(validator.validate(REF, CTX)["verdict"], "accept")

    def test_each_defect_caught_by_its_check(self):
        """Table 9 of the paper: D1..D5 and D8, each detected by the named checks and returned for revision."""
        for f in sorted((HERE / "drafts" / "defects").glob("D*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            res = validator.validate({"interpretation": REF["interpretation"], "recipe": d["recipe"]}, CTX)
            with self.subTest(defect=d["defect"]):
                self.assertEqual(res["verdict"], "revise")
                self.assertEqual(failed(res), set(d["expected_check"]))

    def test_policy_conflict_is_clarify(self):
        c = json.loads((HERE / "drafts" / "I-noqc" / "round0.json").read_text(encoding="utf-8"))
        self.assertEqual(validator.validate(c, CTX)["verdict"], "clarify")

    def test_ungrounded_capability_is_reject(self):
        c = json.loads((HERE / "drafts" / "I-wrench" / "round0.json").read_text(encoding="utf-8"))
        self.assertEqual(validator.validate(c, CTX)["verdict"], "reject")

    def test_no_lease_is_clarify(self):
        ctx = copy.deepcopy(CTX); ctx["K_eng"]["lease"]["declared"] = False
        res = validator.validate(REF, ctx)
        self.assertEqual((res["verdict"], failed(res)), ("clarify", {"V8"}))

    def test_missing_lease_dependency_is_revise(self):
        r = copy.deepcopy(REF)
        for a in r["recipe"]["actions"]:
            for s in a["steps"]:
                if s["name"] == "tighten":
                    s["dep"] = ["configure"]
        self.assertIn("V8", failed(validator.validate(r, CTX)))

    def test_dep_on_later_step_violates_v1(self):
        r = copy.deepcopy(REF)
        r["recipe"]["actions"][0]["steps"][0]["dep"] = ["release"]
        self.assertIn("V1", failed(validator.validate(r, CTX)))


    def test_every_service_declares_its_inputs(self):
        for name, svc in CTX["K_AH"]["services"].items():
            with self.subTest(service=name):
                self.assertIn("requires_results", svc)
                self.assertTrue(set(svc["requires_results"]) <= set(CTX["K_AH"]["services"]))


class CompilerTests(unittest.TestCase):
    def test_compiled_reference_equals_hand_authored_plan(self):
        plan = compiler.compile_recipe(REF["recipe"], CTX)
        ref = json.loads((HERE.parent / "forklift" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(compiler.diff(plan, ref), [])


class PipelineTests(unittest.TestCase):
    def test_stored_runs_match_expectations(self):
        tr = translator.StoredTranslator()
        out = HERE / "out" / "test"
        for i in INTENTS.values():
            with self.subTest(intent=i["id"]):
                r = pipeline.run_intent(i, CTX, tr, 3, out, quiet=True)
                self.assertEqual(r["verdict"], i["expected"])
                if i["expected"] == "accept":
                    self.assertTrue(r["identical_to_reference"])

    def test_revision_bound(self):
        r = pipeline.run_intent(INTENTS["I-tool"], CTX, translator.StoredTranslator(), 2, HERE / "out" / "test",
                                quiet=True)
        self.assertEqual((r["verdict"], r["rounds"]), ("reject", 2))

    def test_prompt_context_hides_validator_fields(self):
        p = json.dumps(translator.context_for_prompt(CTX))
        self.assertNotIn("forbidden_literal_params", p)
        self.assertIn("E3", p)


if __name__ == "__main__":
    unittest.main()
