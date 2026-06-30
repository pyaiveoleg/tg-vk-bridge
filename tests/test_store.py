import json
import os
import tempfile
import unittest

from store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "store.json")
        self.store = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_many_telegram_messages_can_map_to_one_vk_message(self):
        self.store.link_messages(10, 101, 20, 7, direction="tg_to_vk")
        self.store.link_messages(10, 102, 20, 7, direction="tg_to_vk")

        self.assertEqual(
            self.store.tg_all_for_vk(20, 7),
            [[10, 101], [10, 102]],
        )
        self.assertEqual(self.store.vk_for_tg(10, 102), [20, 7])

    def test_old_flat_reverse_mapping_is_supported(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "msg_t2v": {"10:101": [20, 7]},
                "msg_v2t": {"20:7": [10, 101]},
            }, f)
        store = Store(self.path)
        self.assertEqual(store.tg_all_for_vk(20, 7), [[10, 101]])

    def test_deleted_message_lookup_and_unlink(self):
        self.store.link_messages(10, 101, 20, 7, direction="tg_to_vk")
        self.store.link_messages(10, 102, 20, 7, direction="tg_to_vk")

        self.assertEqual(
            self.store.vk_links_for_deleted_tg([102]),
            [(20, 7)],
        )
        self.store.unlink_vk(20, 7)
        self.assertIsNone(self.store.vk_for_tg(10, 101))
        self.assertEqual(self.store.tg_all_for_vk(20, 7), [])

    def test_read_ranges_respect_direction(self):
        self.store.link_messages(10, 101, 20, 7, direction="tg_to_vk")
        self.store.link_messages(10, 102, 20, 8, direction="tg_to_vk")
        self.store.link_messages(10, 103, 20, 9, direction="vk_to_tg")

        self.assertEqual(
            self.store.tg_messages_upto_vk_cmid(20, 8, "tg_to_vk"),
            {10: 102},
        )
        self.assertTrue(
            self.store.has_tg_messages_upto(10, 103, "vk_to_tg")
        )

    def test_sync_state_is_persisted(self):
        self.store.set_sync_state("vk_out_read", 123)
        self.assertEqual(Store(self.path).get_sync_state("vk_out_read"), 123)

    def test_reaction_poll_includes_both_directions(self):
        self.store.link_messages(10, 101, 20, 7, direction="tg_to_vk")
        self.store.link_messages(10, 102, 20, 8, direction="vk_to_tg")
        recent = self.store.recent_tg_links()
        self.assertEqual([item[1] for item in recent], [102, 101])

    def test_reaction_fallback_is_persisted_and_unlinked(self):
        self.store.link_messages(10, 101, 20, 7, direction="tg_to_vk")
        self.store.set_tg_reaction_fallback(10, 101, 20, 8)

        self.assertEqual(
            Store(self.path).get_tg_reaction_fallback(10, 101),
            [20, 8],
        )

        self.store.unlink_vk(20, 7)
        self.assertIsNone(self.store.get_tg_reaction_fallback(10, 101))


if __name__ == "__main__":
    unittest.main()
