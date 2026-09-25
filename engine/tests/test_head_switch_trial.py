import unittest
from app import head_sequence as h
from unittest.mock import patch
p=dict(first_sign=1,switch_ms=9200,settle_ms=3200,target=.18)
frames=[{'ts_ms':i*1400} for i in range(12)]
def check(first=None,second=None,turn=.3):
    vals=[0]*4+(first or [turn]*3)+[0]+(second or [-turn]*4)
    return h.validate_for(frames,[(v,v) for v in vals],p)
class Switch(unittest.TestCase):
    def setUp(self):
        mocked=patch.object(h, "yaw", side_effect=lambda d:d)
        mocked.start();self.addCleanup(mocked.stop)
    def test_one_late_switch(self):self.assertTrue(check(second=[.3,-.3,-.3,-.3])['ok'])
    def test_two_late_samples(self):self.assertFalse(check(second=[.3,.3,-.3,-.3])['ok'])
    def test_reversal(self):self.assertFalse(check(second=[-.3,-.3,.3,-.3])['ok'])
    def test_wrong_first(self):self.assertFalse(check(first=[-.3,.3,.3])['ok'])
    def test_one_frame(self):self.assertFalse(check(second=[.3,0,0,-.3])['ok'])
    def test_nonconsecutive(self):self.assertFalse(check(second=[.3,-.3,0,-.3])['ok'])
    def test_missing_turn(self):self.assertFalse(check(second=[0,0,0,0])['ok'])
    def test_smaller_turn(self):self.assertTrue(check(turn=.19)['ok'])
    def test_tiny_turn(self):self.assertFalse(check(turn=.17)['ok'])
    def test_normal(self):self.assertTrue(check()['ok'])
if __name__=='__main__':unittest.main()
