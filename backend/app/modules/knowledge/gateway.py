"""Talking to the configured model, and nothing else.

The model is an interpreter here: it receives a bounded prompt built from authorized sources
and returns text. The text is validated by `planner`; this module never decides what an action
is, and it never runs one. Keeping the HTTP call separate means the whole workspace can be
tested with a scripted reply, and the real transport path stays exercisable once a person
configures an endpoint.
"""
import asyncio
import json

from ...core.database import HubError
from ...core.outbound import fetch

MAX_REPLY = 64 * 1024


class ModelGateway:
    """Wraps the existing Chat Completions configuration.

    Reuses the assistant's stored endpoint/model/credential and its outbound constraints
    (HTTPS 443, public addresses only, pinned IP, no redirects, no environment proxy).
    """

    def __init__(self, assistant):
        self.assistant = assistant

    def config(self):
        return self.assistant.config()

    def available(self):
        config = self.assistant.config()
        return bool(config['enabled'] and config['has_token'])

    async def complete(self, messages, max_tokens=2000):
        """One non-streaming completion. Never retried automatically.

        `fetch` raises a typed `HubError` on transport problems, and an uncertain outcome is
        reported as such by the caller instead of being retried into a duplicate side effect.
        """
        config = await asyncio.to_thread(self.assistant.config_row)
        if not config['enabled']:
            raise HubError('AI_DISABLED', 409)
        token = await asyncio.to_thread(self.assistant.token, config['credential_ref'])
        body = {'model': config['model'], 'messages': messages, 'stream': False, 'max_tokens': max_tokens}
        raw = await fetch(config['endpoint'], body=body, token=token, max_bytes=MAX_REPLY)
        try:
            data = json.loads(raw)
            content = data['choices'][0]['message']['content']
        except (ValueError, TypeError, KeyError, IndexError):
            raise HubError('AI_RESPONSE_INVALID', 503) from None
        if not isinstance(content, str) or not content.strip():
            raise HubError('AI_RESPONSE_INVALID', 503)
        return content

    def signature(self):
        """The endpoint+model+version an outbound grant must match."""
        config = self.assistant.config_row()
        return {'endpoint': config['endpoint'], 'model': config['model'], 'version': config['version']}
