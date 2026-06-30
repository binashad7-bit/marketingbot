import json
import unittest
from types import SimpleNamespace

from src.personalization import GeminiClient, LeadResearcher, OutreachPersonalizer


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class StaticResearcher:
    def research(self, lead):
        return {'name': lead.school_name, 'evidence': []}


class StaticClient:
    available = True

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def generate_json(self, prompt, **kwargs):
        self.calls += 1
        return self.result


class PersonalizationTests(unittest.TestCase):
    def test_openai_json_provider_uses_structured_chat_completion(self):
        expected = {'posts': [{'caption': 'Specific insight'}]}
        session = FakeSession([
            FakeResponse(200, {
                'choices': [{'message': {'content': json.dumps(expected)}}]
            })
        ])
        client = GeminiClient(api_keys=[], session=session)

        with unittest.mock.patch('src.personalization.Config.OPENAI_API_KEY', 'test-key'):
            result = client.generate_json('prompt', provider='openai')

        self.assertEqual(result, expected)
        self.assertEqual(session.calls[0][0], 'https://api.openai.com/v1/chat/completions')
        payload = session.calls[0][1]['json']
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertIn('max_completion_tokens', payload)

    def test_gemini_rotates_after_quota_error(self):
        result = {
            'subject': 'A relevant idea',
            'email_body': 'Hello there',
            'whatsapp_message': 'Hello from CreatifyBD',
            'observations': [],
            'confidence': 'medium'
        }
        session = FakeSession([
            FakeResponse(429),
            FakeResponse(200, {
                'candidates': [{'content': {'parts': [{'text': json.dumps(result)}]}}]
            })
        ])
        client = GeminiClient(api_keys=['first', 'second'], session=session)

        self.assertEqual(client.generate_json('prompt')['subject'], 'A relevant idea')
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1]['headers']['x-goog-api-key'], 'first')
        self.assertEqual(session.calls[1][1]['headers']['x-goog-api-key'], 'second')

    def test_personalizer_caches_copy_for_both_channels(self):
        result = {
            'subject': 'A specific idea',
            'email_body': 'A concise evidence-backed email.',
            'whatsapp_message': 'Hello, this is CreatifyBD. May I share an idea?',
            'observations': ['The site has no clear service CTA.'],
            'confidence': 'high'
        }
        client = StaticClient(result)
        personalizer = OutreachPersonalizer(client=client, researcher=StaticResearcher())
        personalizer.enabled = True
        lead = SimpleNamespace(id=7, school_name='Example Business')

        first = personalizer.create(lead)
        second = personalizer.create(lead)

        self.assertEqual(first, second)
        self.assertEqual(client.calls, 1)

    def test_private_urls_are_rejected(self):
        self.assertFalse(LeadResearcher._is_public_http_url('http://127.0.0.1/admin'))
        self.assertFalse(LeadResearcher._is_public_http_url('http://localhost:5000'))
        self.assertTrue(LeadResearcher._is_public_http_url('https://example.com'))

    def test_facebook_content_is_cached(self):
        client = StaticClient({
            'content': 'A useful organic marketing lesson for business owners.',
            'image_prompt': 'Clean square editorial image.'
        })
        personalizer = OutreachPersonalizer(client=client, researcher=StaticResearcher())
        personalizer.enabled = True

        first = personalizer.create_facebook_post()
        second = personalizer.create_facebook_post()

        self.assertEqual(first, second)
        self.assertEqual(client.calls, 1)


if __name__ == '__main__':
    unittest.main()
