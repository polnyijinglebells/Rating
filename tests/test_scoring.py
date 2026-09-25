import unittest

from rating_app.scoring import calculate


class ScoringTests(unittest.TestCase):
    def test_positive_without_cap(self):
        self.assertEqual(calculate(0.5, 7, None), 3.5)

    def test_positive_cap(self):
        self.assertEqual(calculate(0.1, 8, 0.2), 0.2)

    def test_negative_cap(self):
        self.assertEqual(calculate(-0.25, 9, -1), -1)

    def test_negative_quantity_is_not_allowed(self):
        self.assertEqual(calculate(1, -2, 1), 0)


if __name__ == "__main__":
    unittest.main()
