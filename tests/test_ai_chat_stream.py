"""Streaming answers must survive reasoning-only output and report empty replies."""

import io
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from openai import BadRequestError

from src.summarize.deepseek_backend import OpenAICompatSummarizer
from src.summarize.errors import LLMContextOverflowError, LLMResponseError


def chunk(content=None, reasoning=None, finish=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=content, reasoning_content=reasoning),
        finish_reason=finish,
    )])


def stream(*chunks):
    result = MagicMock()
    result.__iter__.return_value = iter(chunks)
    return result


class TestThinkingStream(unittest.TestCase):
    def setUp(self):
        self.backend = object.__new__(OpenAICompatSummarizer)
        self.backend.model = 'test-model'
        self.backend.extra_body = {'custom_option': True}
        self.backend.client = MagicMock()
        self.create = self.backend.client.chat.completions.create

    def run_stream(self):
        return list(self.backend._call_chat_api_stream('Synthetic context', [
            {'role': 'user', 'content': 'Synthetic question'},
        ]))

    def test_normal_stream_skips_usage_chunk_and_closes(self):
        response = stream(chunk('答'), chunk('案', finish='stop'), SimpleNamespace(choices=[]))
        self.create.return_value = response
        self.assertEqual(self.run_stream(), ['答', '案'])
        response.close.assert_called_once()
        self.assertEqual(self.create.call_count, 1)

    def test_reasoning_only_retries_without_exposing_reasoning(self):
        first = stream(chunk(reasoning='private reasoning'), chunk(finish='length'))
        second = stream(chunk('最终回复'), chunk(finish='stop'))
        self.create.side_effect = [first, second]
        self.assertEqual(self.run_stream(), ['最终回复'])
        request = self.create.call_args.kwargs
        self.assertEqual(request['max_tokens'], 8192)
        self.assertEqual(request['extra_body'], {
            'custom_option': True, 'thinking': {'type': 'disabled'},
        })
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_unsupported_thinking_option_uses_larger_budget(self):
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        error = BadRequestError('unsupported thinking parameter', response=httpx.Response(400, request=request), body=None)
        self.create.side_effect = [stream(chunk(reasoning='reasoning', finish='length')), error, stream(chunk('回复'))]
        self.assertEqual(self.run_stream(), ['回复'])
        self.assertEqual(self.create.call_args.kwargs['extra_body'], {'custom_option': True})
        self.assertEqual(self.create.call_args.kwargs['max_tokens'], 8192)

    def test_other_bad_request_is_not_retried(self):
        request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
        error = BadRequestError('quota exhausted', response=httpx.Response(400, request=request), body=None)
        self.create.side_effect = [stream(chunk(reasoning='reasoning')), error]
        with self.assertRaises(BadRequestError):
            self.run_stream()
        self.assertEqual(self.create.call_count, 2)

    def test_empty_or_whitespace_stream_is_failure(self):
        for response in (stream(), stream(chunk('  ', finish='stop'))):
            with self.subTest(response=response):
                self.create.return_value = response
                with self.assertRaisesRegex(LLMResponseError, '未返回回复正文'):
                    self.run_stream()
                response.close.assert_called_once()

    def test_empty_retry_is_failure(self):
        self.create.side_effect = [stream(chunk(reasoning='reasoning', finish='length')), stream(chunk(finish='stop'))]
        with self.assertRaisesRegex(LLMResponseError, '未返回回复正文'):
            self.run_stream()
        self.assertEqual(self.create.call_count, 2)

    def test_visible_partial_answer_is_not_replayed(self):
        self.create.return_value = stream(chunk('部分回答'), chunk(finish='length'))
        self.assertEqual(self.run_stream(), ['部分回答'])
        self.assertEqual(self.create.call_count, 1)

    def test_business_error_with_http_200_is_not_skipped(self):
        self.create.return_value = stream(SimpleNamespace(
            choices=None, base_resp={'status_code': 2013, 'status_msg': 'context length exceeds limit'},
        ))
        with self.assertRaises(LLMContextOverflowError):
            self.run_stream()

    def test_generator_cancellation_closes_provider_connection(self):
        response = stream(chunk('第一段'), chunk('第二段'))
        self.create.return_value = response
        tokens = self.backend._call_chat_api_stream('context', [])
        self.assertEqual(next(tokens), '第一段')
        tokens.close()
        response.close.assert_called_once()


class TestChatStreamEvents(unittest.TestCase):
    def invoke(self, tokens):
        from src.web import ai_chat
        from src.summarize import base
        health = base.get_llm_health()
        self.addCleanup(self.restore_health, base, health)
        now = time.time()
        session = ai_chat.AIChatSession(
            session_id='test-session', source_type='group_chat', source_id='synthetic',
            source_name='测试群', context_text='甲: 测试消息', context_tokens=20,
            chat_history=[], estimated_tokens=20, token_budget=900000,
            created_at=now, last_active=now,
        )
        backend = MagicMock(_backend_name='test', model='test-model')
        backend._call_chat_api_stream.return_value = iter(tokens)
        output = io.BytesIO()
        with patch.object(ai_chat, '_get_session', return_value=session), \
             patch.object(ai_chat, 'load_config'), \
             patch.object(ai_chat, 'create_summarizer', return_value=backend), \
             patch.object(ai_chat, 'log_llm_interaction') as log_call, \
             patch('src.web.server.update_status'):
            ai_chat.handle_ai_chat_message_stream({'session_id': 'test-session', 'message': '测试问题'}, output)
        events = []
        for event in output.getvalue().decode().strip().split('\n\n'):
            lines = event.splitlines()
            events.append((lines[0][7:], json.loads(lines[1][6:])))
        return session, events, log_call

    @staticmethod
    def restore_health(base, health):
        with base._llm_health_lock:
            base._llm_health.clear()
            base._llm_health.update(health)

    def test_empty_response_emits_error_and_is_not_saved_as_success(self):
        for tokens in ([], ['', '  ']):
            with self.subTest(tokens=tokens):
                session, events, log_call = self.invoke(tokens)
                self.assertEqual(events[-1][0], 'error')
                self.assertIn('未返回回复正文', events[-1][1]['message'])
                self.assertNotIn('done', [event for event, _ in events])
                self.assertFalse(any(m['role'] == 'assistant' for m in session.chat_history))
                self.assertTrue(log_call.call_args.kwargs['response'].startswith('[Error:'))

    def test_valid_response_saves_answer_and_sends_done(self):
        session, events, log_call = self.invoke(['测试', '回答'])
        self.assertEqual([event for event, _ in events], ['token', 'token', 'done'])
        self.assertEqual(session.chat_history[-1]['content'], '测试回答')
        self.assertEqual(log_call.call_args.kwargs['response'], '测试回答')


if __name__ == '__main__':
    unittest.main()
