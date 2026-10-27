"""Inconclusive ARP evidence must not restart resident discovery."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocpp_discover import service


class PassiveDiscoveryTests(unittest.TestCase):
    def test_rejected_evidence_then_valid_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(service.handoff, "discovered_path", return_value=root / "missing.json"), \
                 patch.object(service, "_wait_then_discover", side_effect=[
                     ValueError("no_unresolved_arp_candidate"),
                     ValueError("ambiguous_arp_candidates"),
                     {"status": "discovered", "chargers": ["charger-1"]},
                 ]) as discover, \
                 patch.object(service.time, "sleep") as sleep:
                result = service.run_service(data_dir=root, persistent_dir=root,
                                             wait_interval=2, max_cycles=3)
            self.assertEqual("discovered", result["status"])
            self.assertEqual(3, discover.call_count)
            self.assertEqual([((2,), {}), ((4,), {})], [(c.args, c.kwargs) for c in sleep.call_args_list])

    def test_inconclusive_evidence_does_not_mutate_or_exit_as_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(service.handoff, "discovered_path", return_value=root / "missing.json"), \
                 patch.object(service, "_wait_then_discover",
                              side_effect=ValueError("ambiguous_arp_candidates")) as discover, \
                 patch.object(service.time, "sleep") as sleep, \
                 patch.object(service.handoff, "_promote_persistent_adaptation") as promote:
                result = service.run_service(data_dir=root, persistent_dir=root,
                                             max_cycles=2, wait_interval=1)
            self.assertEqual("deferred", result["status"])
            self.assertEqual("ambiguous_arp_candidates", result["reason"])
            self.assertEqual(2, discover.call_count)
            sleep.assert_called_once_with(1.0)
            promote.assert_not_called()

    def test_unexpected_value_error_stays_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(service.handoff, "discovered_path", return_value=root / "missing.json"), \
                 patch.object(service, "_wait_then_discover",
                              side_effect=ValueError("invalid_interface")), \
                 patch.object(service.time, "sleep") as sleep:
                with self.assertRaisesRegex(ValueError, "invalid_interface"):
                    service.run_service(data_dir=root, persistent_dir=root, max_cycles=2)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
