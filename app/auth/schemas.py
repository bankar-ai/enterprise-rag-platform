"""Pydantic schemas for the auth API's requests, responses, and resolved identity."""

import uuid
from typing import Literal

from pydantic import BaseModel

Role = Literal["admin", "user"]


class CurrentUser(BaseModel):
    """The authenticated caller, resolved from a validated access token."""

    id: uuid.UUID
    role: Role
