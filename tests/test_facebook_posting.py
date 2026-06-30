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

    def test_low_quality_calendar_posts_get_one_repair_pass(self):
        poster = FacebookPoster()
        poster.gemini = Mock(available=True)
        strong_caption = (
            'Before a founder spends more on reach, the website should make the buying decision easier. '
            'A simple review can expose where trust is leaking: unclear service language, weak proof, slow '
            'mobile pages, a buried contact path, or content that explains features without showing the '
            'customer outcome. Start with one page and check it like a serious buyer who has five tabs open. '
            'What problem is solved, why is this team credible, what happens next, and what concern remains '
            'unanswered? Fixing those answers often makes every later SEO post, social campaign, and paid ad '
            'work harder because the audience lands on clarity instead of confusion.'
        )
        repaired_caption = (
            'If a business page feels active but not persuasive, the problem is usually not frequency. It is '
            'the missing bridge between content and customer decision. Review the last ten posts and mark each '
            'one as awareness, trust, comparison, or action. If most posts only announce services, the audience '
            'has little reason to save, comment, or remember the brand. Start building a weekly pattern: one '
            'post that diagnoses a real website issue, one that teaches an SEO or content decision, one that '
            'shows how trust is created before a buyer contacts you. Which decision should your next post help '
            'a customer make with more confidence?'
        )
        poster.gemini.generate_json.side_effect = [
            {'posts': [
                {
                    'caption': strong_caption,
                    'hook': 'Before a founder spends more on reach',
                    'takeaway': 'Review trust, clarity, and next-step friction before scaling promotion.',
                    'image_prompt': 'Premium editorial still life of a homepage audit desk with clear decision notes, no text.',
                    'quality_score': 10,
                },
                {
                    'caption': 'Generic marketing advice.',
                    'hook': 'Generic hook',
                    'takeaway': 'Generic takeaway',
                    'image_prompt': 'Generic image',
                    'quality_score': 10,
                },
            ]},
            {'posts': [
                {
                    'caption': repaired_caption,
                    'hook': 'If a business page feels active but not persuasive',
                    'takeaway': 'Map posts to customer decisions instead of posting service announcements.',
                    'image_prompt': 'Premium overhead campaign photograph of a social calendar mapped to buyer decisions, no text.',
                    'quality_score': 10,
                },
            ]},
        ]

        slots = [
            poster._now() + timedelta(days=1),
            poster._now() + timedelta(days=1, hours=2),
        ]

        posts = poster._generate_posts_for_slots(slots)

        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]['caption'], strong_caption)
        self.assertEqual(posts[1]['caption'], repaired_caption)
        self.assertEqual(poster.gemini.generate_json.call_count, 2)
        self.assertIn('repair pass', poster.gemini.generate_json.call_args.args[0])

    def test_calendar_batch_failure_retries_individual_slots(self):
        poster = FacebookPoster()
        worksheet = Mock(title='FacebookPosts')
        slots = [
            poster._now() + timedelta(days=1),
            poster._now() + timedelta(days=1, hours=2),
        ]
        post = {
            'audience_stage': 'problem_aware',
            'marketing_goal': 'educate',
            'pillar': 'website_conversion',
            'format': 'checklist',
            'hook': 'Before scaling content, audit the page experience',
            'takeaway': 'Fix clarity and trust before increasing acquisition volume.',
            'caption': 'A practical strategic caption that already passed quality review.',
            'image_prompt': 'Premium editorial desk scene with conversion audit materials, no text.',
            'quality_score': 9,
        }

        with (
            patch.object(poster, '_worksheet', return_value=worksheet),
            patch.object(poster, '_rows', return_value=[]),
            patch.object(poster, '_planned_slots', return_value=slots),
            patch.object(
                poster,
                '_generate_posts_for_slots',
                side_effect=[RuntimeError('bad batch'), [post], RuntimeError('bad slot')]
            ) as generate,
        ):
            result = poster.ensure_content_calendar(horizon_days=1)

        self.assertEqual(result['created'], 1)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(generate.call_count, 3)
        worksheet.append_rows.assert_called_once()
        self.assertEqual(len(worksheet.append_rows.call_args.args[0]), 1)


if __name__ == '__main__':
    unittest.main()
