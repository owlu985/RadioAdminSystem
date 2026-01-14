from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

PRIMARY_DECKS = ("music", "psa")
OVERLAY_KINDS = ("sweeper", "voicetrack")


@dataclass(frozen=True)
class CuePoints:
    cue_in: float = 0.0
    cue_out: Optional[float] = None
    intro: Optional[float] = None
    outro: Optional[float] = None
    start_next: Optional[float] = None

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "CuePoints":
        if not payload:
            return cls()
        return cls(
            cue_in=float(payload.get("cue_in") or 0.0),
            cue_out=_as_float(payload.get("cue_out")),
            intro=_as_float(payload.get("intro")),
            outro=_as_float(payload.get("outro")),
            start_next=_as_float(payload.get("start_next")),
        )


@dataclass(frozen=True)
class QueueItem:
    kind: str
    title: str = ""
    duration: float = 0.0
    cues: CuePoints = field(default_factory=CuePoints)

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "QueueItem":
        return cls(
            kind=(payload.get("kind") or "").lower(),
            title=payload.get("title") or "",
            duration=float(payload.get("duration") or 0.0),
            cues=CuePoints.from_dict(payload.get("cues")),
        )


@dataclass(frozen=True)
class OverlayPlan:
    status: str
    start_in: float
    window: Tuple[float, float] | None
    duration: float
    span_transition: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if self.window is not None:
            payload["window"] = {"start": self.window[0], "end": self.window[1]}
        return payload


@dataclass(frozen=True)
class FadePlan:
    action: str
    start_in: Optional[float] = None
    duration: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ShowAutomatorService:
    SESSION_KEY = "show_automator"

    def __init__(self, session_state: Dict[str, Any]):
        self.session_state = session_state

    @classmethod
    def from_session(cls, session: Dict[str, Any]) -> "ShowAutomatorService":
        state = session.get(cls.SESSION_KEY)
        if not isinstance(state, dict):
            state = {}
            session[cls.SESSION_KEY] = state
        return cls(state)

    def record_plan(self, session: Dict[str, Any], plan: Dict[str, Any]) -> None:
        plan["updated_at"] = datetime.utcnow().isoformat()
        session[self.SESSION_KEY] = plan
        self.session_state.clear()
        self.session_state.update(plan)

    def next_primary_deck(self) -> str:
        last_deck = self.session_state.get("last_primary_deck")
        if last_deck in PRIMARY_DECKS:
            return PRIMARY_DECKS[1] if last_deck == PRIMARY_DECKS[0] else PRIMARY_DECKS[0]
        return PRIMARY_DECKS[0]

    def mark_primary_played(self, deck: str) -> None:
        if deck in PRIMARY_DECKS:
            self.session_state["last_primary_deck"] = deck

    def pause_automation(self, reason: str) -> None:
        self.session_state["automation_paused"] = True
        self.session_state["pause_reason"] = reason

    def resume_automation(self) -> None:
        self.session_state["automation_paused"] = False
        self.session_state.pop("pause_reason", None)

    def automation_paused(self) -> bool:
        return bool(self.session_state.get("automation_paused"))


@dataclass(frozen=True)
class AutomatorConfig:
    fade_duration: float = 3.0
    crossfade_duration: float = 2.0
    minimum_overlay_gap: float = 0.5


def plan_automation_step(
    *,
    session: Dict[str, Any],
    current: Optional[QueueItem],
    next_item: Optional[QueueItem],
    overlay_item: Optional[QueueItem],
    current_position: float,
    config: AutomatorConfig | None = None,
) -> Dict[str, Any]:
    """
    Plan automation actions and store decisions in session.
    """
    config = config or AutomatorConfig()
    service = ShowAutomatorService.from_session(session)
    if next_item and next_item.kind == "stop":
        service.pause_automation("stop_item")
        plan = {
            "paused": True,
            "pause_reason": "stop_item",
            "next_primary_deck": service.next_primary_deck(),
            "overlay": OverlayPlan(
                status="deferred",
                start_in=0.0,
                window=None,
                duration=overlay_item.duration if overlay_item else 0.0,
                reason="automation_paused",
            ).to_dict(),
            "fade": FadePlan(action="none", reason="automation_paused").to_dict(),
            "countdowns": {},
        }
        service.record_plan(session, plan)
        return plan

    if service.automation_paused():
        plan = {
            "paused": True,
            "pause_reason": service.session_state.get("pause_reason"),
            "next_primary_deck": service.next_primary_deck(),
            "overlay": OverlayPlan(
                status="deferred",
                start_in=0.0,
                window=None,
                duration=overlay_item.duration if overlay_item else 0.0,
                reason="automation_paused",
            ).to_dict(),
            "fade": FadePlan(action="none", reason="automation_paused").to_dict(),
            "countdowns": {},
        }
        service.record_plan(session, plan)
        return plan

    overlay_plan = _plan_overlay(
        current=current,
        next_item=next_item,
        overlay_item=overlay_item,
        current_position=current_position,
        config=config,
    )
    fade_plan = _plan_fade(current=current, next_item=next_item, current_position=current_position, config=config)

    countdowns = {}
    if overlay_plan.status == "scheduled":
        countdowns["overlay_in"] = overlay_plan.start_in
    if fade_plan.start_in is not None:
        countdowns["fade_in"] = fade_plan.start_in

    plan = {
        "paused": False,
        "next_primary_deck": service.next_primary_deck(),
        "overlay": overlay_plan.to_dict(),
        "fade": fade_plan.to_dict(),
        "countdowns": countdowns,
    }
    service.record_plan(session, plan)
    return plan


def _plan_overlay(
    *,
    current: Optional[QueueItem],
    next_item: Optional[QueueItem],
    overlay_item: Optional[QueueItem],
    current_position: float,
    config: AutomatorConfig,
) -> OverlayPlan:
    if not overlay_item or overlay_item.kind not in OVERLAY_KINDS:
        return OverlayPlan(status="idle", start_in=0.0, window=None, duration=0.0, reason="no_overlay")

    duration = max(0.0, overlay_item.duration)
    if not current:
        return OverlayPlan(status="deferred", start_in=0.0, window=None, duration=duration, reason="no_current")

    intro_window = _intro_window(current, current_position)
    outro_window = _outro_window(current, current_position)
    next_intro_len = _next_intro_length(next_item)

    if intro_window and duration <= _window_length(intro_window):
        return OverlayPlan(
            status="scheduled",
            start_in=0.0,
            window=intro_window,
            duration=duration,
            reason="intro_window",
        )

    if outro_window and duration <= _window_length(outro_window):
        start_in = max(0.0, outro_window[0] - current_position)
        return OverlayPlan(
            status="scheduled",
            start_in=start_in,
            window=outro_window,
            duration=duration,
            reason="outro_window",
        )

    if outro_window and next_intro_len > 0:
        combined = _window_length(outro_window) + next_intro_len
        if duration <= combined:
            start_in = max(0.0, outro_window[0] - current_position)
            return OverlayPlan(
                status="scheduled",
                start_in=start_in,
                window=(outro_window[0], outro_window[1] + next_intro_len),
                duration=duration,
                span_transition=True,
                reason="outro_to_intro",
            )

    defer_to = _next_safe_window_start(current, current_position)
    return OverlayPlan(
        status="deferred",
        start_in=max(0.0, defer_to - current_position),
        window=None,
        duration=duration,
        reason="no_safe_window",
    )


def _plan_fade(
    *,
    current: Optional[QueueItem],
    next_item: Optional[QueueItem],
    current_position: float,
    config: AutomatorConfig,
) -> FadePlan:
    if not current:
        return FadePlan(action="none", reason="no_current")

    cues = current.cues
    if cues.start_next is not None:
        return FadePlan(
            action="crossfade",
            start_in=max(0.0, cues.start_next - current_position),
            duration=config.crossfade_duration,
            reason="start_next",
        )

    if cues.outro is not None and next_item and next_item.cues.intro is not None:
        return FadePlan(
            action="crossfade",
            start_in=max(0.0, cues.outro - current_position),
            duration=config.crossfade_duration,
            reason="outro_intro",
        )

    if cues.cue_out is not None:
        start_in = max(0.0, cues.cue_out - current_position - config.fade_duration)
        return FadePlan(action="fade_out", start_in=start_in, duration=config.fade_duration, reason="cue_out")

    return FadePlan(action="none", reason="no_cues")


def _intro_window(current: QueueItem, current_position: float) -> Optional[Tuple[float, float]]:
    cues = current.cues
    if cues.intro is None:
        return None
    end = min(cues.intro, current.duration or cues.intro)
    if end <= current_position:
        return None
    return (current_position, end)


def _outro_window(current: QueueItem, current_position: float) -> Optional[Tuple[float, float]]:
    cues = current.cues
    if cues.outro is None:
        return None
    end = current.cues.cue_out or current.duration or cues.outro
    start = max(cues.outro, current_position)
    if end <= start:
        return None
    return (start, end)


def _next_intro_length(next_item: Optional[QueueItem]) -> float:
    if not next_item or next_item.cues.intro is None:
        return 0.0
    return max(0.0, next_item.cues.intro)


def _next_safe_window_start(current: QueueItem, current_position: float) -> float:
    cues = current.cues
    if cues.outro is not None and cues.outro > current_position:
        return cues.outro
    if cues.cue_out is not None and cues.cue_out > current_position:
        return cues.cue_out
    return current.duration or current_position


def _window_length(window: Tuple[float, float]) -> float:
    return max(0.0, window[1] - window[0])


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
