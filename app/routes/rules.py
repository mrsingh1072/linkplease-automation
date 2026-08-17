import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from ..database import get_db

router = APIRouter()


class RuleRequest(BaseModel):
    keyword: str
    dm_message: str

    @field_validator("keyword")
    @classmethod
    def keyword_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("keyword must not be empty")
        return v

    @field_validator("dm_message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("dm_message must not be empty")
        return v


class RuleResponse(BaseModel):
    rule_id: str
    keyword: str
    dm_message: str


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(body: RuleRequest) -> RuleResponse:
    db = get_db()
    rule_id = str(uuid.uuid4())
    await db.rules.insert_one(
        {
            "rule_id": rule_id,
            "keyword": body.keyword,
            "dm_message": body.dm_message,
            "created_at": datetime.now(timezone.utc),
        }
    )
    return RuleResponse(rule_id=rule_id, keyword=body.keyword, dm_message=body.dm_message)
