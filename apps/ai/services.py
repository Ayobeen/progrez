"""Optional structured AI gateway. All responses are validated before use.

No model response writes to the database. Deterministic content is always available.
"""
import json
import logging
from urllib.request import Request, urlopen
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, field_validator
from apps.core.content import FRICTIONS

log = logging.getLogger('progrez')
PROMPT_VERSION = 'progrez-v1'

class StrictOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')

class PlanCopy(StrictOutput):
    title: str = Field(min_length=3, max_length=160)
    reason: str = Field(min_length=3, max_length=800)
    encouragement: str = Field(min_length=3, max_length=300)

class FrictionInterpretation(StrictOutput):
    primary_friction: str
    confidence: float = Field(ge=0, le=1)
    confirmation_question: str = Field(min_length=5, max_length=500)

    @field_validator('primary_friction')
    @classmethod
    def allowed(cls, v):
        if v not in FRICTIONS:
            raise ValueError('Unknown friction')
        return v

class DiagnosticInterpretation(StrictOutput):
    recommended_session_minutes: int = Field(ge=5, le=60)
    notes: str = Field(max_length=500)

class AdaptedExplanation(StrictOutput):
    adapted_content: str = Field(min_length=10, max_length=6000)
    source_reference: str = Field(min_length=3, max_length=200)

def generate(task, context, schema):
    if not settings.AI_SERVICE_URL:
        return None
    if not settings.AI_SERVICE_URL.startswith('https://'):
        log.warning('ai_fallback reason=non_https_gateway')
        return None
    payload = {'task': task, 'prompt_version': PROMPT_VERSION, 'instruction': 'Use only provided source material. Do not introduce new educational facts. Return JSON matching the schema. Never change intervention, scoring, or database state.', 'context': context, 'response_schema': schema.model_json_schema()}
    request = Request(settings.AI_SERVICE_URL, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {settings.AI_SERVICE_KEY}'}, method='POST')
    try:
        with urlopen(request, timeout=8) as response:
            data = response.read(32769)
        if len(data) > 32768:
            raise ValueError('Response too large')
        output = schema.model_validate_json(data)
        log.info('ai_validated task=%s version=%s', task, PROMPT_VERSION)
        return output.model_dump()
    except Exception as exc:
        log.warning('ai_fallback task=%s error_type=%s', task, type(exc).__name__)
        return None
