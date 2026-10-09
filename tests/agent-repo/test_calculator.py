import unittest
from calculator import total_with_tax
class CalculatorTests(unittest.TestCase):
    def test_tax(self): self.assertEqual(total_with_tax(100,0.1),110)
    def test_zero(self): self.assertEqual(total_with_tax(100,0),100)
    def test_negative_rejected(self):
        with self.assertRaises(ValueError): total_with_tax(-1,0.1)
if __name__=='__main__': unittest.main()
