from typing import Dict, List

from textual.reactive import reactive
from textual.widgets import Static


KAI_FRAMES: Dict[str, List[str]] = {
    "idle": [
        "   \\|/   \n .- * -.  \n<  o o  > \n `-._.-'  \n    >_    ",
        "    |    \n .- * -.  \n<  - -  > \n `-._.-'  \n    >_    ",
        "   /|\\   \n .- * -.  \n<  o o  > \n `-._.-'  \n    _<    ",
    ],
    "listening": [
        "  .\\|/.  \n<.- * -.> \n<  o o  > \n `-.^.-'  \n   >__    ",
        "  *\\|/*  \n<.- * -.> \n<  o o  > \n `-.^.-'  \n    >_    ",
    ],
    "connecting": [
        "--------- \n .- * -.  \n<  o o  > \n `-._.-'  \n    >_    ",
        "          \n--------- \n<  o o  > \n `-._.-'  \n    >_    ",
        "          \n .- * -.  \n--------- \n `-._.-'  \n    >_    ",
    ],
    "thinking": [
        "*  \\|/    \n .- + -.  \n<  o O  > \n `-._.-'  \n   >...   ",
        "   *|\\    \n .- x -.  \n<  O o  > \n `-._.-'  \n   >...   ",
        "    /|\\  *\n .- + -.  \n<  o O  > \n `-._.-'  \n   >...   ",
        "   /|*     \n .- x -.  \n<  O o  > \n `-._.-'  \n   >...   ",
    ],
    "streaming": [
        "   \\|/   \n .- * -.  \n<  o o  > \n `-.v.-'  \n   >_     ",
        "   \\|/   \n .- * -.  \n<  o o  > \n `-.v.-'  \n    >_    ",
        "   \\|/   \n .- * -.  \n<  o o  > \n `-.v.-'  \n     >_   ",
    ],
    "tool_wait": [
        "   \\|/   \n .- ? -.  \n<  o o  > \n `-.^.-'  \n  [y/n]   ",
    ],
    "tool_run": [
        "+  \\|/  *\n .- # -.  \n<  O O  > \n `-.v.-'  \n  <exec>  ",
        "*  /|\\  +\n .- % -.  \n<  O O  > \n `-.v.-'  \n  <exec>  ",
    ],
    "compressing": [
        "*       * \n .- * -.  \n<  o o  > \n `-._.-'  \n < fold > ",
        "  *   *   \n .- + -.  \n<  o o  > \n `-._.-'  \n  <fold>  ",
        "    *     \n .- . -.  \n<  - -  > \n `-._.-'  \n   fold   ",
    ],
    "success": [
        "* * | * * \n *- * -*  \n<  ^ ^  > \n `-.v.-'  \n   done   ",
        "   \\|/   \n .- * -.  \n<  ^ ^  > \n `-.v.-'  \n    ok    ",
    ],
    "error": [
        " ! \\|/   \n.-  x - . \n<  > <  > \n `-.~.-'  \n  error   ",
        "   \\|/ ! \n .- x  -. \n<  > <  > \n `-.~.-'  \n  error   ",
    ],
}

KAI_WIDTH = 11
KAI_HEIGHT = 5


def normalize_frame(frame: str) -> str:
    lines = frame.splitlines()[:KAI_HEIGHT]
    lines.extend([""] * (KAI_HEIGHT - len(lines)))
    return "\n".join(line[:KAI_WIDTH].ljust(KAI_WIDTH) for line in lines)


KAI_FRAMES = {
    state: [normalize_frame(frame) for frame in frames]
    for state, frames in KAI_FRAMES.items()
}


STATE_COLORS = {
    "idle": "#66d9ef",
    "listening": "#66d9ef",
    "connecting": "#f6c177",
    "thinking": "#f6c177",
    "streaming": "#66d9ef",
    "tool_wait": "#f6c177",
    "tool_run": "#c6a0f6",
    "compressing": "#c6a0f6",
    "success": "#8bd5ca",
    "error": "#ed8796",
}


class KaiMascot(Static):
    state = reactive("idle")

    def __init__(self, *, reduced_motion: bool = False, **kwargs):
        super().__init__(KAI_FRAMES["idle"][0], **kwargs)
        self.reduced_motion = reduced_motion
        self.frame_index = 0
        self._timer = None

    def on_mount(self):
        self.styles.width = 11
        self.styles.height = 5
        self.styles.color = STATE_COLORS["idle"]
        if not self.reduced_motion:
            self._timer = self.set_interval(0.12, self.advance_frame)

    def watch_state(self, state: str):
        self.frame_index = 0
        self.styles.color = STATE_COLORS.get(state, STATE_COLORS["idle"])
        self.update(KAI_FRAMES.get(state, KAI_FRAMES["idle"])[0])

    def set_state(self, state: str):
        self.state = state if state in KAI_FRAMES else "idle"

    def advance_frame(self):
        frames = KAI_FRAMES.get(self.state, KAI_FRAMES["idle"])
        if self.state == "idle" and self.frame_index % 3:
            return
        self.frame_index = (self.frame_index + 1) % len(frames)
        self.update(frames[self.frame_index])
