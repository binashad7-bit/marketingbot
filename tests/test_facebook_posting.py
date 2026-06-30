import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

from src.facebook_posting import FacebookPoster


class FacebookPostingTests(unittest.TestCase):
    def test_photo_failure_does_not_publish_text_only(self):
        poster = FacebookPoster()
        poster.page_id = 'page-id'
        poster.access_token = 'page-token'
        failed = Mock(status_code=400, text='photo rejected')

        with patch('src.facebook_posting.requests.post', return_value=failed) as request:
            success, post_id = poster.post_to_facebook(
                'Caption',
                image_bytes=b'not-a-real-image',
                mime_type='image/png',
            )

        self.assertFalse(success)
        self.assertIsNone(post_id)
        self.assertEqual(request.call_count, 1)
        self.assertTrue(request.call_args.args[0].endswith('/photos'))

    def test_missing_credentials_are_not_reported_as_posted(self):
        poster = FacebookPoster()
        poster.page_id = None
        poster.access_token = None

        with patch.object(poster, '_queue_post', return_value='queued-id'):
            success, post_id = poster.post_to_facebook('Caption')

        self.assertFalse(success)
        self.assertIsNone(post_id)
        self.assertIn('credential missing', poster._last_facebook_error.lower())

    def test_model_self_score_cannot_override_low_quality_caption(self):
        poster = FacebookPoster()
        cleaned = poster._clean_generated_post({
            'caption': 'Generic marketing advice.',
            'hook': 'Generic hook',
            'takeaway': 'Generic takeaway',
            'image_prompt': 'Generic image',
            'quality_score': 10,
        })

        self.assertLess(cleaned['quality_score'], 9)

    def test_due_selector_ignores_future_posts(self):
        poster = FacebookPoster()
        now = poster._now()
        rows = [
            {
                'status': poster.PENDING,
                'quality_score': 10,
                'scheduled_at': (now + timedelta(hours=1)).isoformat(),
            },
        ]

        self.assertIsNone(poster._first_publishable_due(rows, now))

    def test_posting_path_does_not_mutate_calendar(self):
        poster = FacebookPoster()
        worksheet = Mock()
        with (
            patch.object(poster, '_worksheet', return_value=worksheet),
            patch.object(poster, '_rows', return_value=[]),
            patch.object(poster, 'ensure_content_calendar') as ensure_calendar,
        ):
            self.assertFalse(poster.post_next_approved())

        ensure_calendar.assert_not_called()

    def test_image_review_requires_every_quality_dimension(self):
        review = {
            'score': 9,
            'decision': 'pass',
            'critical_failures': [],
            'dimension_scores': {
                'photorealism': 9,
                'anatomy_geometry': 9,
                'lighting_materials': 9,
                'concept_relevance': 7,
                'composition': 9,
                'brand_distinctiveness': 9,
            },
        }

        self.assertFalse(FacebookPoster._image_review_passes(review))

    def test_production_calendar_never_uses_low_quality_static_fallback(self):
        poster = FacebookPoster()
        poster.gemini = Mock(available=True)
        poster.gemini.generate_json.return_value = {
            'posts': [{
                'caption': 'Generic advice.',
                'hook': 'Generic hook',
                'takeaway': 'Generic takeaway',
                'image_prompt': 'Generic image',
                'quality_score': 10,
            }]
        }

        with (
            patch('src.facebook_posting.Config.ENVIRONMENT', 'production'),
            patch('src.facebook_posting.Config.FACEBOOK_AUTONOMOUS_MODE', True),
        ):
            with self.assertRaises(RuntimeError):
                poster._generate_posts_for_slots([poster._now() + timedelta(days=1)])


if __name__ == '__main__':
    unittest.main()
