"""Structured payloads exchanged between LangGraph nodes and the LLM."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

ActionVerb = Literal[
    "seize", "sabotage", "negotiate", "threaten", "bribe", "ally", "investigate",
    "fortify", "steal", "attack", "recruit", "spread_rumor", "hide", "flee", "observe", "trade",
]


class ForecastRequest(BaseModel):
    session_number: int = Field(ge=0, description="Session that just ended, e.g. 14")
    start_day: int = Field(ge=0, description="In-world day the session started")
    end_day: int = Field(ge=0, description="In-world day the session ended")
    forecast_days: int = Field(default=7, ge=1, le=90, description="Off-screen days to simulate until the next session")
    session_summary: str = Field(min_length=1)
    base_branch: str = "main"
    seed: int | None = Field(default=None, description="Mock-mode randomness seed (defaults to session number)")

    @field_validator("end_day")
    @classmethod
    def _order(cls, v: int, info) -> int:
        start = info.data.get("start_day")
        if start is not None and v < start:
            raise ValueError("end_day must be >= start_day")
        return v


# ------------------------------------------------------------------ session delta
class PartyAction(BaseModel):
    summary: str = Field(description="One sentence describing what the party did")
    action_type: Literal["steal", "kill", "help", "harm", "bribe", "discover", "travel", "other"] = "other"
    target_ids: list[str] = Field(default_factory=list, description="Existing node ids affected")
    location_id: str | None = None
    visibility: Literal["public", "secret"] = "public"
    trust_delta: int = Field(default=0, ge=-100, le=100, description="Change in targets' trust toward the party")
    item_transferred_id: str | None = Field(default=None, description="Item the party took, if any")
    killed_ids: list[str] = Field(default_factory=list)


class SessionDelta(BaseModel):
    party_actions: list[PartyAction] = Field(default_factory=list)


# ------------------------------------------------------------------ agents
class AgentIntent(BaseModel):
    agent_id: str
    action: ActionVerb
    target_id: str | None = Field(description="A node id the agent KNOWS/BELIEVES (or controls/relates to)")
    resources_spent: int = Field(default=0, ge=0, le=100)
    reasoning: str = Field(description="1-3 sentences of in-character reasoning, citing desires/beliefs")
    public_or_secret: Literal["public", "secret"] = "secret"
    responding_to: str | None = Field(default=None, description="Event id this reacts to, if any")
    advances_goal_id: str | None = None


class AgentIntentLLM(BaseModel):
    """What the LLM is asked for (agent_id is filled in by code)."""
    action: ActionVerb
    target_id: str | None
    resources_spent: int = Field(default=0, ge=0, le=100)
    reasoning: str
    public_or_secret: Literal["public", "secret"] = "secret"
    responding_to: str | None = None
    advances_goal_id: str | None = None


# ------------------------------------------------------------------ arbiter
class RelationshipShift(BaseModel):
    from_id: str
    to_id: str
    delta: int = Field(ge=-100, le=100)
    label: str | None = None
    reason: str = ""


class ControlChange(BaseModel):
    target_id: str
    new_controller_id: str | None
    kind: Literal["CONTROLS", "OWNS"] = "CONTROLS"


class NewGoal(BaseModel):
    owner_id: str
    title: str
    description: str = ""
    target_id: str | None = None
    priority: float = Field(default=0.7, ge=0, le=1)
    clock_segments: int = Field(default=4, ge=1, le=12)


class NewDesire(BaseModel):
    from_id: str
    kind: Literal["WANTS", "NEEDS", "FEARS"]
    target_id: str | None = None
    target_text: str | None = None
    priority: float = Field(default=0.6, ge=0, le=1)
    reason: str = ""


class KnowledgeGain(BaseModel):
    agent_id: str
    fact_id: str
    kind: Literal["KNOWS", "BELIEVES"] = "KNOWS"
    confidence: float = Field(default=1.0, ge=0, le=1)


class WorldEventDraft(BaseModel):
    title: str
    summary: str
    event_type: Literal["faction_move", "npc_action", "conflict", "goal_progress", "goal_achieved", "death", "world"] = "npc_action"
    day: int
    actor_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    location_id: str | None = None
    visibility: Literal["public", "secret", "leaked"] = "public"
    caused_by: str | None = Field(default=None, description="Event id that triggered this")
    from_intents: list[str] = Field(default_factory=list, description="agent ids whose intents produced this")
    relationship_shifts: list[RelationshipShift] = Field(default_factory=list)
    control_changes: list[ControlChange] = Field(default_factory=list)
    killed_ids: list[str] = Field(default_factory=list)
    goal_progress: dict[str, int] = Field(default_factory=dict, description="goal id -> clock segments gained")
    resource_changes: dict[str, int] = Field(default_factory=dict, description="faction id -> resource delta")
    witnesses: list[str] = Field(default_factory=list, description="agent ids who learn of this event")
    new_goals: list[NewGoal] = Field(default_factory=list)
    new_desires: list[NewDesire] = Field(default_factory=list)
    knowledge_gained: list[KnowledgeGain] = Field(default_factory=list)


class ArbiterResult(BaseModel):
    events: list[WorldEventDraft]


# ------------------------------------------------------------------ rumors
class RumorDraft(BaseModel):
    about_event: str
    location_id: str
    accuracy: Literal["True", "Exaggerated", "False"]
    text: str
    dialogue: str
    speaker: str | None = None


class RumorBatch(BaseModel):
    rumors: list[RumorDraft]
