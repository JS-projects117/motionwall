import itertools
import unittest

from motionwall import policy
from motionwall.config import Config


class PolicyTests(unittest.TestCase):
    def test_all_combinations(self):
        flags = ("user_play", "locked", "idle", "on_battery", "occluded")
        for values in itertools.product([False, True], repeat=5):
            state = policy.PolicyState(**dict(zip(flags, values)))
            for cfg_bits in itertools.product([False, True], repeat=4):
                cfg = Config(pause_on_lock=cfg_bits[0], pause_on_idle=cfg_bits[1],
                             pause_on_battery=cfg_bits[2], pause_on_fullscreen=cfg_bits[3])
                expected = (state.user_play and not (state.locked and cfg.pause_on_lock)
                            and not (state.idle and cfg.pause_on_idle)
                            and not (state.on_battery and cfg.pause_on_battery)
                            and not (state.occluded and cfg.pause_on_fullscreen))
                play, reason = policy.decide(state, cfg)
                self.assertEqual(play, expected, (state, cfg_bits))
                self.assertEqual(reason is None, play)

    def test_reason_priority(self):
        cfg = Config()
        self.assertEqual(policy.decide(policy.PolicyState(user_play=False, locked=True), cfg), (False, "user"))
        self.assertEqual(policy.decide(policy.PolicyState(locked=True, idle=True), cfg), (False, "locked"))
        self.assertEqual(policy.decide(policy.PolicyState(idle=True, occluded=True), cfg), (False, "idle"))
        self.assertEqual(policy.decide(policy.PolicyState(on_battery=True), cfg), (False, "battery"))
        self.assertEqual(policy.decide(policy.PolicyState(occluded=True), cfg), (False, "fullscreen"))
        self.assertEqual(policy.decide(policy.PolicyState(), cfg), (True, None))
        for reason in ("user", "locked", "idle", "battery", "fullscreen"):
            self.assertIn(reason, policy.REASON_LABELS)


if __name__ == "__main__":
    unittest.main()
