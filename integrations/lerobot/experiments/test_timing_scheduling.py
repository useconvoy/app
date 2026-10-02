import unittest

from jetson_timing.scheduling import Chunk, Playback


class PlaybackTests(unittest.TestCase):
    def test_delayed_result_does_not_replay_elapsed_prefix(self):
        p = Playback(1)
        c = Chunk(0, 10, .1, ((.1, 0, 0, 1), (.2, 0, 0, 1), (.3, 0, 0, -1)))
        self.assertTrue(p.accept(c, 10.21))
        action, evidence = p.action(10.21)
        self.assertEqual(action[0], .3)
        self.assertEqual(evidence["chunk_index"], 2)
        self.assertTrue(p.action(10.22)[1]["fallback"])
        self.assertTrue(p.action(10.31)[1]["fallback"])

    def test_stale_new_result_does_not_destroy_valid_buffer(self):
        p = Playback(1)
        self.assertTrue(p.accept(Chunk(0, 10, .1, ((0, 0, 0, 1),) * 10), 10.01))
        self.assertFalse(p.accept(Chunk(1, 10.02, .1, ((1, 0, 0, 1),)), 10.3))
        self.assertEqual(p.action(10.31)[1]["source_sequence"], 0)
        self.assertFalse(p.accept(Chunk(0, 10.3, .1, ((1, 0, 0, 1),)), 10.31))

    def test_outage_exhausts_buffer_without_repeating_motion(self):
        p = Playback(.15)
        p.accept(Chunk(0, 0, .1, ((1, 0, 0, -1),) * 10), .01)
        self.assertEqual(p.action(.11)[0][0], 1)
        self.assertEqual(p.action(.16)[0], (0, 0, 0, -1))
        self.assertTrue(p.action(9)[1]["fallback"])

    def test_invalid_and_future_chunks(self):
        with self.assertRaises(ValueError):
            Chunk(0, 0, .1, ((float("nan"), 0, 0, 0),))
        with self.assertRaises(ValueError):
            Chunk(0, 0, .1, ((2, 0, 0, 0),))
        self.assertFalse(Playback(1).accept(Chunk(0, 2, .1, ((0, 0, 0, 0),)), 1))


if __name__ == "__main__":
    unittest.main()
