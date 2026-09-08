"""Guard the interface between raw-evidence verification and fidelity gates."""
import importlib.util
import json
import os
from pathlib import Path
import unittest

DOMAIN = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('s2p_acceptance', DOMAIN / 'scripts/acceptance.py')
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)

class FidelityRequirementTests(unittest.TestCase):
    def test_every_fidelity_requirement_resolves_to_observed_checks(self):
        evidence_path = Path(os.environ.get('S2P_JOURNEY_EVIDENCE', DOMAIN / 'artifacts/journey-latest.json'))
        observed = acceptance.verify_journey(json.loads(evidence_path.read_text()))
        fidelity = json.loads((DOMAIN / 'spec/runtime-fidelity.json').read_text())
        requirements = {r for n in fidelity['actual_source_symbols'] + fidelity['replacement_nodes'] for r in n['trace_requirements']}
        self.assertEqual(requirements, set(acceptance.TRACE_REQUIREMENT_CHECKS))
        for requirement in requirements:
            for check in acceptance.TRACE_REQUIREMENT_CHECKS[requirement]:
                self.assertIn(check, observed['checks'], f'{requirement} refers to an obsolete verifier check')
                self.assertTrue(observed['checks'][check], requirement)
