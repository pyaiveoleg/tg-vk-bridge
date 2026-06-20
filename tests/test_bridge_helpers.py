import unittest

import bridge
import reactions


class BridgeHelperTests(unittest.TestCase):
    def test_wall_links(self):
        match = bridge._wall_link_match(
            "https://vk.com/feed?w=wall-123_456"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ("-123", "456"))

    def test_best_video_url(self):
        self.assertEqual(
            bridge._best_vk_video_url({
                "files": {
                    "mp4_360": "small.mp4",
                    "mp4_1080": "large.mp4",
                    "hls": "playlist.m3u8",
                }
            }),
            "large.mp4",
        )

    def test_direct_video_links(self):
        self.assertEqual(
            bridge._video_link_parts("https://vk.com/video-123_456"),
            (-123, 456, None),
        )
        self.assertEqual(
            bridge._video_link_parts(
                "https://vk.com/feed?z=video12_34%2Fabc"
            ),
            (12, 34, None),
        )
        self.assertEqual(
            bridge._video_link_parts("https://vk.com/clip-77_88"),
            (-77, 88, None),
        )

    def test_nail_reaction_uses_community_safe_fallback(self):
        self.assertEqual(reactions.vk_to_emoji(36), "💅")
        self.assertEqual(reactions.emoji_to_vk("💅"), 15)


if __name__ == "__main__":
    unittest.main()
