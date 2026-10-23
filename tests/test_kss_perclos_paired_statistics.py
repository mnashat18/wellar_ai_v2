import unittest

from research.kss_perclos_benchmark_v1.paired_statistics import paired_cluster_bootstrap_ci


class PairedClusterBootstrapTests(unittest.TestCase):
    def test_negative_effect_favors_ridge_and_is_deterministic(self):
        ridge = [1.0, 2.0, 3.0]
        baseline = [2.0, 3.0, 4.0]
        first = paired_cluster_bootstrap_ci(ridge, baseline, replicates=1000, seed=17)
        second = paired_cluster_bootstrap_ci(ridge, baseline, replicates=1000, seed=17)
        self.assertEqual(first, second)
        self.assertEqual(first["mean_paired_difference_ridge_minus_baseline"], -1.0)
        self.assertEqual(first["lower_95"], -1.0)
        self.assertEqual(first["upper_95"], -1.0)
        self.assertIn("negative favors Ridge", first["sign_convention"])

    def test_paired_resampling_keeps_within_participant_differences(self):
        result = paired_cluster_bootstrap_ci(
            [1.0, 4.0, 3.0], [3.0, 3.0, 5.0], replicates=1000, seed=4
        )
        self.assertAlmostEqual(result["mean_paired_difference_ridge_minus_baseline"], -1.0)
        self.assertEqual(result["clusters"], 3)

    def test_rejects_mismatched_or_nonfinite_pairs(self):
        with self.assertRaises(ValueError):
            paired_cluster_bootstrap_ci([1.0, 2.0], [1.0])
        with self.assertRaises(ValueError):
            paired_cluster_bootstrap_ci([1.0, float("nan")], [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
